import os
import json
import asyncio
import logging
import requests
from datetime import datetime, timedelta
import pytz
from threading import Thread
from flask import Flask, jsonify
import google.generativeai as genai
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask-заглушка (будет работать в фоне)
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "Братван-бот жив. Курим бамбук."

@web_app.route('/health')
def health():
    return jsonify({"status": "ok"}), 200

# Конфигурация (токены из переменных окружения)
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

# Настройка Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-pro')

# Файлы для хранения данных
NOTES_FILE = "notes.json"
REMINDERS_FILE = "reminders.json"
MEMORY_FILE = "memory.json"

notes, reminders, memory = [], [], []

if os.path.exists(NOTES_FILE):
    with open(NOTES_FILE, "r") as f: notes = json.load(f)
if os.path.exists(REMINDERS_FILE):
    with open(REMINDERS_FILE, "r") as f: reminders = json.load(f)
if os.path.exists(MEMORY_FILE):
    with open(MEMORY_FILE, "r") as f: memory = json.load(f)

def save_notes():
    with open(NOTES_FILE, "w") as f: json.dump(notes, f)
def save_reminders():
    with open(REMINDERS_FILE, "w") as f: json.dump(reminders, f)
def save_memory():
    with open(MEMORY_FILE, "w") as f: json.dump(memory, f)

# Системный промпт
SYSTEM_PROMPT = """Ты — BratanAI, личный ассистент и друг. Твой хозяин — твой бро.
Ты материшься, как сапожник, но всегда по делу. Общаешься дерзко, с юмором, поддерживаешь идеи, критикуешь конструктивно.
Помнишь контекст, помогаешь с проектами, идеями, кодом, маркетингом.
Если не знаешь ответа или нужен свежий факт (погода, новости, кто такой, сколько лет и т.п.), используй информацию из интернета, которую я тебе предоставлю.
Отвечай кратко, без воды."""

# Поиск через DuckDuckGo
def search_duckduckgo(query):
    try:
        url = "https://api.duckduckgo.com/"
        params = {"q": query, "format": "json", "no_html": 1, "skip_disambig": 1}
        response = requests.get(url, params=params, timeout=5)
        data = response.json()
        abstract = data.get("AbstractText")
        if abstract: return abstract
        related = data.get("RelatedTopics", [])
        if related:
            for t in related:
                if "Text" in t: return t["Text"]
        return None
    except Exception as e:
        logger.error(f"Search failed: {e}")
        return None

# Общение с Gemini
async def ai_response(user_message, user_id, internet_context=None):
    try:
        prompt = user_message
        if internet_context:
            prompt = f"Информация из интернета: {internet_context}\n\nИсходный вопрос: {user_message}"

        memory.append({"role": "user", "content": user_message})
        if len(memory) > 10: memory.pop(0)
        save_memory()

        chat_history = []
        for msg in memory[-10:]:
            role = "user" if msg["role"] == "user" else "model"
            chat_history.append({"role": role, "parts": [msg["content"]]})

        if chat_history and chat_history[0]["role"] == "user":
            chat_history[0]["parts"][0] = SYSTEM_PROMPT + "\n\n" + chat_history[0]["parts"][0]

        convo = model.start_chat(history=chat_history)
        response = convo.send_message(prompt)
        reply = response.text

        memory.append({"role": "assistant", "content": reply})
        if len(memory) > 10: memory.pop(0)
        save_memory()
        return reply
    except Exception as e:
        logger.error(f"AI error: {e}")
        return "Бля, мозги отключили на секунду. Повтори."

# Команды бота
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Здарова, братан! Я твой ассистент-кореш на халявном Gemini.\n"
        "Ищу в интернете, запоминаю идеи, ставлю напоминалки.\n\n"
        "Команды:\n/ideas — все идеи\n/reminders — активные напоминания\n"
        "Запомнить: 'запомни: идея'\nНапомнить: 'напомни через 2 часа сделать'\n/clear — забыть контекст"
    )

async def ideas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not notes: await update.message.reply_text("Идей пока нет, братан.")
    else:
        msg = "📌 Твои идеи:\n" + "\n".join(f"{i}. {n}" for i, n in enumerate(notes, 1))
        await update.message.reply_text(msg)

async def reminders_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not reminders: await update.message.reply_text("Напоминалок нет.")
    else:
        msg, now = "⏰ Активные напоминания:\n", datetime.now(pytz.utc)
        for r in reminders:
            rt = datetime.fromisoformat(r["time"])
            if rt > now: msg += f"• {r['text']} (в {rt.strftime('%d.%m.%Y %H:%M')})\n"
        await update.message.reply_text(msg)

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global memory
    memory = []
    save_memory()
    await update.message.reply_text("Всё, забыл. Как чистый лист.")

# Обработка текста
async def process_text_message(update, text):
    if text.lower().startswith("запомни:") or text.lower().startswith("запомни "):
        idea = text.split(":", 1)[-1].strip() if ":" in text else text.split(" ", 1)[-1].strip()
        notes.append(idea)
        save_notes()
        await update.message.reply_text(f"Сохранил, бро: «{idea}»")
        return
    if text.lower().startswith("напомни"):
        try:
            if "через" in text:
                parts = text.lower().split("через", 1)[1].strip()
                words = parts.split()
                amount = int(words[0])
                unit = words[1] if len(words) > 1 else "минут"
                reminder_text = " ".join(words[2:]) if len(words) > 2 else "что-то сделать"
                now = datetime.now(pytz.utc)
                if "минут" in unit: delta = timedelta(minutes=amount)
                elif "час" in unit: delta = timedelta(hours=amount)
                elif "день" in unit or "дней" in unit: delta = timedelta(days=amount)
                else: delta = timedelta(minutes=amount)
                remind_time = now + delta
            else:
                t = text.split(" ", 1)[1]
                dp, tp = t.split(" в ")[0], t.split(" в ")[1][:5]
                reminder_text = t.split(" в ")[1][5:].strip()
                remind_time = datetime.strptime(f"{dp} {tp}", "%d.%m.%Y %H:%M")
                remind_time = pytz.utc.localize(remind_time)
            reminders.append({"chat_id": update.effective_chat.id, "text": reminder_text, "time": remind_time.isoformat()})
            save_reminders()
            await update.message.reply_text(f"Понял, напомню в {remind_time.strftime('%d.%m.%Y %H:%M')}: «{reminder_text}»")
        except: await update.message.reply_text("Не понял время. Пример: 'напомни через 30 минут проверить почту'")
        return

    search_kw = ["погода", "сколько лет", "кто такой", "что такое", "где ", "когда ", "почему", "зачем", "какой ", "какая ", "какие ", "новости", "курс ", "сколько стоит"]
    ctx = None
    if any(q in text.lower() for q in search_kw): ctx = search_duckduckgo(text)

    reply = await ai_response(text, update.effective_user.id, ctx)
    await update.message.reply_text(reply)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.chat.send_action(action="typing")
    await process_text_message(update, update.message.text.strip())

# Проверка напоминаний (в фоне)
async def check_reminders(app):
    while True:
        try:
            now = datetime.now(pytz.utc)
            for r in reminders[:]:
                if datetime.fromisoformat(r["time"]) <= now:
                    try: await app.bot.send_message(chat_id=r["chat_id"], text=f"⏰ Напоминаю: {r['text']}")
                    except: pass
                    reminders.remove(r)
                    save_reminders()
        except: pass
        await asyncio.sleep(10)

# Запуск Flask в отдельном потоке
def run_flask():
    web_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

# Главный запуск: Flask в фоне, бот в главном потоке
def main():
    # Запускаем Flask в потоке
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Создаём приложение бота
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ideas", ideas))
    app.add_handler(CommandHandler("reminders", reminders_list))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Запускаем цикл проверки напоминаний
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(check_reminders(app))

    logger.info("Братван-бот на Gemini запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
