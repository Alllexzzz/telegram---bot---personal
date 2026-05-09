import os
import json
import asyncio
import logging
import io
import requests
from datetime import datetime, timedelta
import pytz
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import speech_recognition as sr
from pydub import AudioSegment
import imageio_ffmpeg
import os
ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
AudioSegment.converter = ffmpeg_path
import google.generativeai as genai

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация из переменных окружения
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

# Настройка Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')  # бесплатная, быстрая, умная

# Файлы для хранения данных
NOTES_FILE = "notes.json"
REMINDERS_FILE = "reminders.json"
MEMORY_FILE = "memory.json"

# Загрузка заметок, напоминаний и памяти
if os.path.exists(NOTES_FILE):
    with open(NOTES_FILE, "r") as f:
        notes = json.load(f)
else:
    notes = []

if os.path.exists(REMINDERS_FILE):
    with open(REMINDERS_FILE, "r") as f:
        reminders = json.load(f)
else:
    reminders = []

if os.path.exists(MEMORY_FILE):
    with open(MEMORY_FILE, "r") as f:
        memory = json.load(f)
else:
    memory = []

def save_notes():
    with open(NOTES_FILE, "w") as f:
        json.dump(notes, f)

def save_reminders():
    with open(REMINDERS_FILE, "w") as f:
        json.dump(reminders, f)

def save_memory():
    with open(MEMORY_FILE, "w") as f:
        json.dump(memory, f)

# Системный промпт с матом и возможностью поиска
SYSTEM_PROMPT = """Ты — BratanAI, личный ассистент и друг. Твой хозяин — твой бро.
Ты материшься, как сапожник, но всегда по делу. Общаешься дерзко, с юмором, поддерживаешь идеи, критикуешь конструктивно.
Помнишь контекст, помогаешь с проектами, идеями, кодом, маркетингом.
Если не знаешь ответа или нужен свежий факт (погода, новости, кто такой, сколько лет и т.п.), используй информацию из интернета, которую я тебе предоставлю.
Отвечай кратко, без воды."""

# Функция поиска через DuckDuckGo (бесплатно)
def search_duckduckgo(query):
    try:
        url = "https://api.duckduckgo.com/"
        params = {
            "q": query,
            "format": "json",
            "no_html": 1,
            "skip_disambig": 1
        }
        response = requests.get(url, params=params, timeout=5)
        data = response.json()
        abstract = data.get("AbstractText")
        if abstract:
            return abstract
        related = data.get("RelatedTopics", [])
        if related:
            for topic in related:
                if "Text" in topic:
                    return topic["Text"]
        return None
    except Exception as e:
        logger.error(f"Search failed: {e}")
        return None

# Функция общения с Gemini (мозги)
async def ai_response(user_message, user_id, internet_context=None):
    try:
        # Если есть контекст из интернета, подставляем
        if internet_context:
            prompt = f"Информация из интернета: {internet_context}\n\nИсходный вопрос: {user_message}"
        else:
            prompt = user_message

        # Сохраняем сообщение в память
        memory.append({"role": "user", "content": user_message})
        if len(memory) > 10:
            memory.pop(0)
        save_memory()

        # Формируем историю для Gemini
        chat_history = []
        for msg in memory[-10:]:
            role = "user" if msg["role"] == "user" else "model"
            chat_history.append({"role": role, "parts": [msg["content"]]})

        # Gemini не поддерживает system prompt в истории, поэтому добавляем его в начало первого сообщения
        if not chat_history or chat_history[0]["role"] != "user":
            # Вставляем системный промпт в первое сообщение
            prompt = SYSTEM_PROMPT + "\n\n" + prompt
        else:
            # Объединяем системный промпт с контекстом
            chat_history[0]["parts"][0] = SYSTEM_PROMPT + "\n\n" + chat_history[0]["parts"][0]

        convo = model.start_chat(history=chat_history)
        response = convo.send_message(prompt)
        reply = response.text

        memory.append({"role": "assistant", "content": reply})
        if len(memory) > 10:
            memory.pop(0)
        save_memory()
        return reply
    except Exception as e:
        logger.error(f"AI error: {e}")
        return "Бля, мозги отключили на секунду. Повтори."

# Обработчик команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Здарова, братан! Я твой ассистент-кореш на халявных мозгах Gemini.\n"
        "Слышу голосовые, вижу кружочки, ищу в интернете, запоминаю идеи и ставлю напоминалки.\n\n"
        "Команды:\n"
        "/ideas — все идеи\n"
        "/reminders — активные напоминания\n"
        "Чтобы запомнить: 'запомни: твоя идея'\n"
        "Напоминание: 'напомни через 2 часа сделать то-то'\n"
        "/clear — забыть контекст"
    )

# Обработчик идей
async def ideas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not notes:
        await update.message.reply_text("Идей пока нет, братан.")
    else:
        msg = "📌 Твои идеи:\n" + "\n".join(f"{i}. {n}" for i, n in enumerate(notes, 1))
        await update.message.reply_text(msg)

# Обработчик напоминаний
async def reminders_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not reminders:
        await update.message.reply_text("Напоминалок нет.")
    else:
        msg = "⏰ Активные напоминания:\n"
        now = datetime.now(pytz.utc)
        for rem in reminders:
            remind_time = datetime.fromisoformat(rem["time"])
            if remind_time > now:
                msg += f"• {rem['text']} (в {remind_time.strftime('%d.%m.%Y %H:%M')})\n"
        await update.message.reply_text(msg)

# Обработчик очистки контекста
async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global memory
    memory = []
    save_memory()
    await update.message.reply_text("Всё, забыл. Как чистый лист.")

# Распознавание речи из аудиофайла
def transcribe_audio(file_path):
    recognizer = sr.Recognizer()
    try:
        audio = AudioSegment.from_ogg(file_path)
        wav_data = io.BytesIO()
        audio.export(wav_data, format="wav")
        wav_data.seek(0)
        with sr.AudioFile(wav_data) as source:
            audio_data = recognizer.record(source)
        text = recognizer.recognize_google(audio_data, language="ru-RU")
        return text
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        return None

# Обработчик голосовых сообщений
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.chat.send_action(action="typing")
    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)
    await file.download_to_drive("voice.ogg")
    text = transcribe_audio("voice.ogg")
    if not text:
        await update.message.reply_text("Не расслышал, братан. Повтори.")
        return
    await process_text_message(update, text)

# Обработчик видеосообщений (кружочков)
async def handle_video_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.chat.send_action(action="typing")
    video_note = update.message.video_note
    file = await context.bot.get_file(video_note.file_id)
    await file.download_to_drive("circle.mp4")
    try:
        audio = AudioSegment.from_file("circle.mp4", format="mp4")
        audio.export("circle.ogg", format="ogg")
        text = transcribe_audio("circle.ogg")
    except Exception as e:
        logger.error(f"Circle error: {e}")
        text = None
    if not text:
        await update.message.reply_text("В кружочке не разобрал речь, сорян.")
        return
    await process_text_message(update, text)

# Общая функция обработки текста
async def process_text_message(update, text):
    # Запомни
    if text.lower().startswith("запомни:") or text.lower().startswith("запомни "):
        idea = text.split(":", 1)[-1].strip() if ":" in text else text.split(" ", 1)[-1].strip()
        notes.append(idea)
        save_notes()
        await update.message.reply_text(f"Сохранил, бро: «{idea}»")
        return

    # Напомни
    if text.lower().startswith("напомни"):
        try:
            if "через" in text:
                parts = text.lower().split("через", 1)[1].strip()
                words = parts.split()
                amount = int(words[0])
                unit = words[1] if len(words) > 1 else "минут"
                reminder_text = " ".join(words[2:]) if len(words) > 2 else "что-то сделать"
                now = datetime.now(pytz.utc)
                if "минут" in unit:
                    delta = timedelta(minutes=amount)
                elif "час" in unit:
                    delta = timedelta(hours=amount)
                elif "день" in unit or "дней" in unit:
                    delta = timedelta(days=amount)
                else:
                    delta = timedelta(minutes=amount)
                remind_time = now + delta
            else:
                text_without_command = text.split(" ", 1)[1]
                date_part = text_without_command.split(" в ")[0]
                time_part = text_without_command.split(" в ")[1][:5]
                reminder_text = text_without_command.split(" в ")[1][5:].strip()
                remind_time = datetime.strptime(f"{date_part} {time_part}", "%d.%m.%Y %H:%M")
                remind_time = pytz.utc.localize(remind_time)
            reminders.append({
                "chat_id": update.effective_chat.id,
                "text": reminder_text,
                "time": remind_time.isoformat()
            })
            save_reminders()
            await update.message.reply_text(f"Понял, напомню в {remind_time.strftime('%d.%m.%Y %H:%M')}: «{reminder_text}»")
        except Exception as e:
            await update.message.reply_text("Не понял время. Пример: 'напомни через 30 минут проверить почту'")
        return

    # Поиск в интернете при необходимости
    search_keywords = ["погода", "сколько лет", "кто такой", "что такое", "где ", "когда ", "почему", "зачем", "какой ", "какая ", "какие ", "новости", "курс ", "сколько стоит"]
    internet_context = None
    if any(q in text.lower() for q in search_keywords):
        internet_context = search_duckduckgo(text)

    reply = await ai_response(text, update.effective_user.id, internet_context)
    await update.message.reply_text(reply)

# Обработчик обычных текстовых сообщений
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.chat.send_action(action="typing")
    await process_text_message(update, update.message.text.strip())

# Фоновая проверка напоминаний
async def check_reminders(app):
    while True:
        try:
            now = datetime.now(pytz.utc)
            for rem in reminders[:]:
                remind_time = datetime.fromisoformat(rem["time"])
                if remind_time <= now:
                    try:
                        await app.bot.send_message(chat_id=rem["chat_id"], text=f"⏰ Напоминаю: {rem['text']}")
                    except Exception as e:
                        logger.error(f"Failed to send reminder: {e}")
                    reminders.remove(rem)
                    save_reminders()
        except Exception as e:
            logger.error(f"Reminder loop error: {e}")
        await asyncio.sleep(10)

# Запуск
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ideas", ideas))
    app.add_handler(CommandHandler("reminders", reminders_list))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.VIDEO_NOTE, handle_video_note))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    loop = asyncio.get_event_loop()
    loop.create_task(check_reminders(app))

    logger.info("Братван-бот на Gemini запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
