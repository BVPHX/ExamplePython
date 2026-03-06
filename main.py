import os
import json
import discord
from discord.ext import commands
from mistralai import Mistral
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла
load_dotenv()

# Конфигурация из переменных окружения
BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
MISTRAL_API_KEY = os.getenv('MISTRAL_API_KEY')
BOT_PREFIX = os.getenv('BOT_PREFIX', '!')  # Значение по умолчанию '!'

# Проверка наличия необходимых токенов
if not BOT_TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN не найден в переменных окружения!")
if not MISTRAL_API_KEY:
    raise ValueError("MISTRAL_API_KEY не найден в переменных окружения!")

# Инициализация бота и клиента Mistral
intents = discord.Intents.all()
bot = commands.Bot(command_prefix=BOT_PREFIX, intents=intents)
mistral_client = Mistral(api_key=MISTRAL_API_KEY)

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
    """Обработка входящих сообщений"""
    # Игнорируем сообщения от самого бота
    if message.author == bot.user:
        return
    
    # Обрабатываем команды
    await bot.process_commands(message)
    
    # Не отвечаем на команды (они уже обработаны выше)
    if message.content.startswith(BOT_PREFIX):
        return
    
        # Получаем ответ от Mistral AI
    response = mistral_client.chat.complete(
        model="mistral-small-latest",
        messages=[
            {
                "role": "user",
                "content": message.content
            }
        ]
    )
    
    ai_response = response.choices[0].message.content
    
    # Отправляем ответ
    await message.channel.send(ai_response)
        

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