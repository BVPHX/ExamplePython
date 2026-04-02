import os
import json
import discord
from discord.ext import commands
from openai import OpenAI 
from dotenv import load_dotenv
import asyncio
from datetime import datetime, timedelta

# Хранилище времени последней активности в канале (ключ: channel.id)
last_activity = {}

# Таймаут в секундах (по умолчанию 5 минут = 300 секунд)
TIMEOUT_SECONDS = int(os.getenv('AUTO_MESSAGE_TIMEOUT', 120))
# Интервал проверки (раз в 30 секунд)
CHECK_INTERVAL = 45
# Загружаем переменные окружения из .env файла
load_dotenv()

prompt_file = os.getenv('SYSTEM_PROMPT_FILE')
if prompt_file and os.path.exists(prompt_file):
    with open(prompt_file, 'r', encoding='utf-8') as f:
        SYSTEM_PROMPT = f.read()

def update_history(channel_id, user_message, author_name, bot_response):
    """
    Сохраняет в историю:
    - сообщение пользователя (с именем)
    - ответ бота
    Обрезает историю до MAX_HISTORY_LENGTH сообщений
    """
    if channel_id not in message_history:
        message_history[channel_id] = []

    # Добавляем сообщение пользователя
    message_history[channel_id].append({
        "role": "user",
        "content": f"[{author_name}]: {user_message}"
    })

    # Добавляем ответ бота (от лица персонажа)
    message_history[channel_id].append({
        "role": "assistant",
        "content": bot_response
    })

    # Оставляем только последние MAX_HISTORY_LENGTH сообщений
    if len(message_history[channel_id]) > MAX_HISTORY_LENGTH:
        message_history[channel_id] = message_history[channel_id][-MAX_HISTORY_LENGTH:]

def build_conversation_messages(channel_id, user_message, author_name):
    """
    Собирает список сообщений для API Claude:
    - системный промпт
    - последние MAX_HISTORY_LENGTH сообщений из истории канала
    - текущее сообщение пользователя (с указанием автора)
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Добавляем предыдущую историю, если есть
    if channel_id in message_history:
        for msg in message_history[channel_id]:
            # Для каждого сохранённого сообщения добавляем роль и контент
            # Контент уже содержит имя автора
            messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })

    # Добавляем текущее сообщение пользователя с его именем
    current_user_msg = f"[{author_name}]: {user_message}"
    messages.append({"role": "user", "content": current_user_msg})

    return messages


# Хранилище истории сообщений для каждого канала
# Ключ: channel.id, значение: список словарей {"role": "user"/"assistant", "content": "текст", "author": "имя"}
message_history = {}

# Максимальное количество СООБЩЕНИЙ в истории (не диалоговых пар)
MAX_HISTORY_LENGTH = 15


# Конфигурация из переменных окружения
BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
AITUNNEL_API_KEY = os.getenv('AITUNNEL_API_KEY')  # <-- новый ключ
BOT_PREFIX = os.getenv('BOT_PREFIX', '!')

# Проверка наличия необходимых токенов
if not BOT_TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN не найден в переменных окружения!")
if not AITUNNEL_API_KEY:
    raise ValueError("AITUNNEL_API_KEY не найден в переменных окружения!")

# Инициализация бота
intents = discord.Intents.all()
bot = commands.Bot(command_prefix=BOT_PREFIX, intents=intents)

async def setup_hook():
    bot.loop.create_task(auto_message_loop())

bot.setup_hook = setup_hook

# --- НАСТРОЙКА КЛИЕНТА ДЛЯ AITUNNEL / CLAUDE ---
# Используем OpenAI-совместимый интерфейс
client = OpenAI(
    api_key=AITUNNEL_API_KEY,
    base_url="https://api.aitunnel.ru/v1/",   # прокси AITUNNEL
)


def load_config():
    """Загрузка дополнительной конфигурации из JSON файла"""
    try:
        with open("config.json", "r") as json_file:
            return json.load(json_file)
    except FileNotFoundError:
        print("config.json не найден, использую только переменные окружения")
        return {}
    except json.JSONDecodeError:
        print("Ошибка при парсинге config.json")
        return {}

@bot.command(name="join")
async def join_voice(ctx):
    """Подключение бота к голосовому каналу"""
    if ctx.author.voice:
        channel = ctx.author.voice.channel
        await channel.connect()
        await ctx.send(f"Подключился к {channel.name}")
    else:
        await ctx.send("Вы не находитесь в голосовом канале!")

@bot.command(name="leave")
async def leave_voice(ctx):
    """Отключение бота от голосового канала"""
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("Отключился от голосового канала")
    else:
        await ctx.send("Бот не находится в голосовом канале!")

@bot.command(name="supporters")
async def show_supporters(ctx):
    """Показать список поддерживающих"""
    supporters_text = "Supporters: \nSxcred \nLamenz \nGuPi_3"
    await ctx.send(supporters_text)

@bot.event
async def on_message(message):
    # Игнорируем сообщения от самого бота
    if message.author == bot.user:
        return

# Обновляем время последней активности для этого канала
    last_activity[message.channel.id] = datetime.now()

    await bot.process_commands(message)

    if message.content.startswith(BOT_PREFIX):
        return

    channel_id = message.channel.id
    author_name = message.author.display_name  # ник в Discord (можно использовать name)

    # Строим историю с текущим сообщением
    conversation = build_conversation_messages(channel_id, message.content, author_name)

    try:
        response = client.chat.completions.create(
            model="anthropic/claude-sonnet-4.6",
            messages=conversation,
            max_tokens=4096,
            temperature=1.1,
        )

        ai_response = response.choices[0].message.content

        # Сохраняем в историю (пользовательское сообщение и ответ бота)
        update_history(channel_id, message.content, author_name, ai_response)

        await message.channel.send(ai_response)

    except Exception as e:
        print(f"Ошибка при вызове Claude через AITUNNEL: {e}")
        await message.channel.send("Зафакапилась...")

async def generate_auto_message(channel_id):
    """Генерирует короткое сообщение от лица персонажа, когда никто не пишет"""
    try:
        # Очень короткий системный промпт для автосообщений
        

        response = client.chat.completions.create(
            model="anthropic/claude-sonnet-4.6",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "Напиши сообщение для пустого чата."}
            ],
            max_tokens=60,  # очень короткий ответ
            temperature=1.2,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        import random
        return random.choice("И тут факап...")

@bot.event
async def on_ready():
    """Событие при успешном запуске бота"""
    print(f"\n✅ Бот {bot.user.name} успешно запущен!")
    print(f"📋 ID: {bot.user.id}")
    print(f"🔧 Префикс: {BOT_PREFIX}")
    print(f"🌐 Серверов: {len(bot.guilds)}")
    print("-" * 30)

async def is_connected(ctx):
    """Проверка подключения бота к голосовому каналу"""
    voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    return voice_client and voice_client.is_connected()

async def auto_message_loop():
    await bot.wait_until_ready()
    print("[AUTO] Фоновая задача запущена, интервал проверки", CHECK_INTERVAL, "сек")
    while not bot.is_closed():
        now = datetime.now()
        print(f"[AUTO] Проверка {len(last_activity)} каналов...")
        for channel_id, last_time in list(last_activity.items()):
            elapsed = (now - last_time).total_seconds()
            print(f"[AUTO] Канал {channel_id}: прошло {elapsed:.1f} сек, порог {TIMEOUT_SECONDS} сек")
            if elapsed >= TIMEOUT_SECONDS:
                channel = bot.get_channel(channel_id)
                if not channel:
                    print(f"[AUTO] Канал {channel_id} не найден, удаляю")
                    del last_activity[channel_id]
                    continue
                # Проверка прав
                if hasattr(channel, 'guild') and channel.guild:
                    me = channel.guild.me
                else:
                    me = channel.me
                if not channel.permissions_for(me).send_messages:
                    print(f"[AUTO] Нет прав на отправку в {channel.name}, пропускаю")
                    continue
                
                print(f"[AUTO] Отправляю автосообщение в {channel.name}")
                msg = await generate_auto_message(channel_id)
                try:
                    await channel.send(msg)
                    last_activity[channel_id] = datetime.now()  # сброс таймера
                except Exception as e:
                    print(f"[AUTO] Ошибка отправки: {e}")
        await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    # Загружаем дополнительную конфигурацию
    config = load_config()

    # Запускаем бота
    try:
        bot.run(BOT_TOKEN)
    except discord.LoginFailure:
        print("❌ Ошибка авторизации! Проверьте токен бота в .env файле.")
    except Exception as e:
        print(f"❌ Ошибка при запуске бота: {e}")
