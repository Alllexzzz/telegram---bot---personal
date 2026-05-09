import os, json, asyncio, logging, requests
from datetime import datetime, timedelta
import pytz
from flask import Flask, request, jsonify
from openai import OpenAI
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- Логи ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Flask-сервер ---
app = Flask(__name__)

# --- Конфигурация ---
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
RENDER_URL = "https://secretar-041o.onrender.com"

# DeepSeek клиент
client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
MODEL = "deepseek-chat"  # или deepseek-coder

# --- Хранилище ---
NOTES_FILE, REMINDERS_FILE, MEMORY_FILE = "notes.json", "reminders.json", "memory.json"
notes, reminders, memory = [], [], []
for fname, lst in ((NOTES_FILE, notes), (REMINDERS_FILE, reminders), (MEMORY_FILE, memory)):
    if os.path.exists(fname):
        with open(fname, "r") as f: lst.extend(json.load(f))

def save(lst, fname):
    with open(fname, "w") as f: json.dump(lst, f)

SYSTEM_PROMPT = """Ты — BratanAI, личный ассистент и друг. Твой хозяин — твой бро.
Ты материшься, как сапожник, но всегда по делу. Общаешься дерзко, с юмором, поддерживаешь идеи, критикуешь конструктивно.
Помнишь контекст, помогаешь с проектами, идеями, кодом, маркетингом.
Если нужен факт (погода, новости, кто такой, сколько лет), используй информацию из интернета, которую я передам.
Отвечай кратко, без воды."""

# --- DuckDuckGo поиск ---
def search_duckduckgo(query):
    try:
        r = requests.get("https://api.duckduckgo.com/",
                         params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
                         timeout=5)
        data = r.json()
        if data.get("AbstractText"):
            return data["AbstractText"]
        for t in data.get("RelatedTopics", []):
            if "Text" in t: return t["Text"]
        return None
    except Exception as e:
        logger.error(f"Search error: {e}")
        return None

# --- AI-ответ через DeepSeek ---
async def ai_response(user_message, user_id, internet_context=None):
    try:
        prompt = user_message
        if internet_context:
            prompt = f"Информация из интернета: {internet_context}\n\nВопрос: {user_message}"

        memory.append({"role": "user", "content": user_message})
        if len(memory) > 10: memory.pop(0)
        save(memory, MEMORY_FILE)

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for m in memory[-10:]:
            role = m["role"] if m["role"] == "user" else "assistant"
            messages.append({"role": role, "content": m["content"]})
        messages.append({"role": "user", "content": prompt})

        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=0.8,
            max_tokens=600
        )
        reply = response.choices[0].message.content

        memory.append({"role": "assistant", "content": reply})
        if len(memory) > 10: memory.pop(0)
        save(memory, MEMORY_FILE)
        return reply
    except Exception as e:
        logger.error(f"AI error: {e}")
        return "Бля, мозги отключили на секунду. Повтори."

# --- Обработчики Telegram (без изменений, но в webhook) ---
# (код дальше без правок, только register_handlers)
async def start(update, context):
    await update.message.reply_text("Здарова! Я ассистент на DeepSeek. Команды: /ideas, /reminders, /clear.\nЗапомнить: 'запомни: ...'\nНапомнить: 'напомни через 30 мин ...")

async def ideas(update, context):
    if not notes:
        await update.message.reply_text("Идей нет.")
    else:
        msg = "📌 Идеи:\n" + "\n".join(f"{i}. {n}" for i,n in enumerate(notes,1))
        await update.message.reply_text(msg)

async def reminders_list(update, context):
    if not reminders:
        await update.message.reply_text("Напоминалок нет.")
    else:
        now = datetime.now(pytz.utc)
        msg = "⏰ Напоминания:\n"
        for r in reminders:
            dt = datetime.fromisoformat(r["time"])
            if dt > now:
                msg += f"• {r['text']} (в {dt.strftime('%d.%m.%Y %H:%M')})\n"
        await update.message.reply_text(msg)

async def clear(update, context):
    memory.clear()
    save(memory, MEMORY_FILE)
    await update.message.reply_text("Контекст очищен.")

async def process_text(update, text):
    if text.lower().startswith(("запомни:", "запомни ")):
        idea = text.split(":",1)[-1].strip() if ":" in text else text[8:].strip()
        notes.append(idea)
        save(notes, NOTES_FILE)
        await update.message.reply_text(f"Сохранено: {idea}")
        return
    if text.lower().startswith("напомни"):
        try:
            if "через" in text:
                parts = text.lower().split("через",1)[1].strip()
                words = parts.split()
                amount = int(words[0])
                unit = words[1] if len(words)>1 else "минут"
                reminder_text = " ".join(words[2:]) if len(words)>2 else "что-то сделать"
                now = datetime.now(pytz.utc)
                if "минут" in unit: delta = timedelta(minutes=amount)
                elif "час" in unit: delta = timedelta(hours=amount)
                elif "день" in unit or "дней" in unit: delta = timedelta(days=amount)
                else: delta = timedelta(minutes=amount)
                remind_time = now + delta
            else:
                t = text.split(" ",1)[1]
                date_part = t.split(" в ")[0]
                time_part = t.split(" в ")[1][:5]
                reminder_text = t.split(" в ")[1][5:].strip()
                remind_time = datetime.strptime(f"{date_part} {time_part}", "%d.%m.%Y %H:%M")
                remind_time = pytz.utc.localize(remind_time)
            reminders.append({"chat_id": update.effective_chat.id, "text": reminder_text, "time": remind_time.isoformat()})
            save(reminders, REMINDERS_FILE)
            await update.message.reply_text(f"Напомню в {remind_time.strftime('%d.%m.%Y %H:%M')}: {reminder_text}")
        except:
            await update.message.reply_text("Формат: напомни через 30 минут сделать звонок")
        return

    # поиск в интернете при вопросительных словах
    ctx = None
    keywords = ["погода","сколько лет","кто такой","что такое","где ","когда ","почему","зачем","какой ","какая ","какие ","новости","курс ","сколько стоит"]
    if any(k in text.lower() for k in keywords):
        ctx = search_duckduckgo(text)

    reply = await ai_response(text, update.effective_user.id, ctx)
    await update.message.reply_text(reply)

async def handle_text(update, context):
    if not update.message: return
    await update.message.chat.send_action("typing")
    await process_text(update, update.message.text.strip())

# --- Фоновая проверка напоминаний (работает и с webhook) ---
async def reminder_loop(app):
    while True:
        try:
            now = datetime.now(pytz.utc)
            for r in reminders[:]:
                if datetime.fromisoformat(r["time"]) <= now:
                    try:
                        await app.bot.send_message(chat_id=r["chat_id"], text=f"⏰ Напоминаю: {r['text']}")
                    except: pass
                    reminders.remove(r)
                    save(reminders, REMINDERS_FILE)
        except: pass
        await asyncio.sleep(10)

# --- Запуск с webhook ---
def main():
    app_telegram = Application.builder().token(TELEGRAM_TOKEN).build()
    app_telegram.add_handler(CommandHandler("start", start))
    app_telegram.add_handler(CommandHandler("ideas", ideas_list))
    app_telegram.add_handler(CommandHandler("reminders", reminders_list))
    app_telegram.add_handler(CommandHandler("clear", clear_context))
    app_telegram.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # --- Обязательная инициализация приложения ---
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(app_telegram.initialize())

    # Фоновая проверка напоминаний
    loop.create_task(reminder_loop(app_telegram))

    # Устанавливаем вебхук
    webhook_url = f"{RENDER_URL}/webhook"
    logger.info(f"Устанавливаю webhook: {webhook_url}")
    loop.run_until_complete(app_telegram.bot.set_webhook(url=webhook_url))

    # --- Flask-обработчик (СИНХРОННЫЙ) ---
    @app.route("/webhook", methods=["POST"])
    def webhook():
        try:
            data = request.get_json()
            update = Update.de_json(data, app_telegram.bot)
            # Запускаем асинхронную обработку в текущем event loop
            future = asyncio.run_coroutine_threadsafe(
                app_telegram.process_update(update),
                loop
            )
            future.result(timeout=10)
            return "ok", 200
        except Exception as e:
            logger.error(f"Webhook error: {e}")
            return "error", 500

    # Запуск Flask
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
