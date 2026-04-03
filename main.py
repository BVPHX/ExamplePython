import os
import json
import discord
from discord.ext import commands
from dotenv import load_dotenv
import asyncio
from datetime import datetime, timedelta
import io
import aiohttp
import asyncio
from collections import deque
import sys
sys.dont_write_bytecode = True

# Хранилище времени последней активности в канале (ключ: channel.id)
last_activity = {}

# Хранилище очередей TTS для каждого голосового канала (ключ: guild.id)
tts_queues = {}
# Хранилище задач обработки очередей (ключ: guild.id)
tts_tasks = {}
# Флаг остановки для каждой очереди
tts_stop_flags = {}

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

async def call_claude_api(messages: list) -> str | None:
    """Отправляет запрос к Claude через AITUNNEL и возвращает ответ"""
    url = "https://api.aitunnel.ru/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {AITUNNEL_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "anthropic/claude-sonnet-4.6",
        "messages": messages,
        "max_tokens": 4096,
        "temperature": 1.1
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"]
                else:
                    text = await resp.text()
                    print(f"Ошибка Claude API: {resp.status} - {text}")
                    return None
    except Exception as e:
        print(f"Исключение при вызове Claude: {e}")
        return None

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
# client = OpenAI(
#     api_key=AITUNNEL_API_KEY,
#     base_url="https://api.aitunnel.ru/v1/",
# )


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

        # Запускаем обработчик очереди TTS для этой гильдии
        guild_id = ctx.guild.id
        if guild_id not in tts_tasks and guild_id not in tts_stop_flags:
            tts_stop_flags[guild_id] = False
            tts_queues[guild_id] = asyncio.Queue()
            task = asyncio.create_task(process_tts_queue(guild_id))
            tts_tasks[guild_id] = task
    else:
        await ctx.send("А ниче тот факт что ты не в канале?")

@bot.command(name="leave")
async def leave_voice(ctx):
    """Отключение бота от голосового канала"""
    if ctx.voice_client:
        # Останавливаем обработку очереди для этой гильдии
        guild_id = ctx.guild.id
        if guild_id in tts_stop_flags:
            tts_stop_flags[guild_id] = True  # сигнал остановки
        if guild_id in tts_queues:
            # добавляем сигнал None, чтобы разблокировать queue.get()
            await tts_queues[guild_id].put(None)

        await ctx.voice_client.disconnect()
        await ctx.send("До связи")
    else:
        await ctx.send("Я и так нигде не сижу")

@bot.command(name="supporters")
async def show_supporters(ctx):
    """Показать список поддерживающих"""
    supporters_text = "Supporters: \nSxcred \nLamenz \nGuPi_3"
    await ctx.send(supporters_text)

async def enqueue_tts(guild_id: int, text: str):
    """Добавляет текст в очередь TTS для указанной гильдии"""
    if guild_id not in tts_queues:
        tts_queues[guild_id] = asyncio.Queue()
    await tts_queues[guild_id].put(text)

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    last_activity[message.channel.id] = datetime.now()
    await bot.process_commands(message)

    if message.content.startswith(BOT_PREFIX):
        return

    channel_id = message.channel.id
    author_name = message.author.display_name
    conversation = build_conversation_messages(channel_id, message.content, author_name)

    ai_response = await call_claude_api(conversation)
    if ai_response is None:
        await message.channel.send("Ошибка при обращении к ИИ.")
        return

    await message.channel.send(ai_response)

    # TTS, если бот в голосовом канале
    voice_client = message.guild.voice_client
    if voice_client and voice_client.is_connected():
        await enqueue_tts(message.guild.id, ai_response)

async def generate_auto_message(channel_id):
    """Генерирует короткое сообщение от лица персонажа, когда никто не пишет"""
    try:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Напиши сообщение для пустого чата."}
        ]
        response_text = await call_claude_api(messages)
        if response_text:
            return response_text.strip()
        else:
            return "Молчание..."
    except Exception as e:
        print(f"Ошибка автосообщения: {e}")
        return "Что-то пошло не так..."

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

async def process_tts_queue(guild_id: int):
    """Постоянно обрабатывает очередь TTS для конкретного голосового канала"""
    await bot.wait_until_ready()
    queue = tts_queues.get(guild_id)
    if not queue:
        return

    while not tts_stop_flags.get(guild_id, False):
        # Получаем следующий текст из очереди (блокирующая операция)
        text = await queue.get()
        if text is None:  # сигнал остановки
            break

        voice_client = bot.get_guild(guild_id).voice_client
        if not voice_client or not voice_client.is_connected():
            # Если бот вышел из канала – очищаем очередь и выходим
            break

        # Если уже что-то играет – ждём (очередь уже работает, но на всякий случай)
        while voice_client.is_playing():
            await asyncio.sleep(0.5)

        # Генерируем аудио через TTS
        audio_data = await generate_tts_audio(text)
        if audio_data is None:
            continue  # ошибка TTS – пропускаем

        # Воспроизводим аудио из байтового потока
        audio_source = discord.FFmpegPCMAudio(io.BytesIO(audio_data), pipe=True)
        # Создаём событие для ожидания окончания воспроизведения
        play_finished = asyncio.Event()

        def after_play(error):
            if error:
                print(f"Ошибка воспроизведения: {error}")
            bot.loop.call_soon_threadsafe(play_finished.set)

        voice_client.play(audio_source, after=after_play)
        await play_finished.wait()  # ждём, пока трек не закончится

        # Отмечаем задачу выполненной
        queue.task_done()

    # Уборка: удаляем очередь и задачу при выходе
    if guild_id in tts_queues:
        del tts_queues[guild_id]
    if guild_id in tts_tasks:
        del tts_tasks[guild_id]
    if guild_id in tts_stop_flags:
        del tts_stop_flags[guild_id]

async def generate_tts_audio(text: str) -> bytes | None:
    print(f"🔊 TTS: Получен запрос для текста: {text[:50]}...")
    voice = os.getenv('TTS_VOICE', 'nova')
    url = "https://api.aitunnel.ru/v1/audio/speech"
    headers = {
        "Authorization": f"Bearer {AITUNNEL_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "tts-1-hd",
        "voice": voice,
        "input": text
    }
    print(f"🚀 TTS: Отправляю запрос в AITUNNEL с голосом {voice}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    audio_data = await resp.read()
                    print(f"✅ TTS: Успешно получен аудиофайл размером {len(audio_data)} байт")
                    return audio_data
                else:
                    error_text = await resp.text()
                    print(f"❌ TTS: Ошибка API! Статус: {resp.status}, Текст: {error_text}")
                    return None
    except Exception as e:
        print(f"❌ TTS: Исключение при сетевом запросе: {type(e).__name__}: {e}")
        return None

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
