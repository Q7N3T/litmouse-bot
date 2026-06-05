import os
import sqlite3

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

from flask import Flask
from threading import Thread

TOKEN = "8292292452:AAF31xt5WbIz3KyPzgx2KwS77DfkxGh-jl4"
ADMIN_IDS = {804851530, 5242178843}
CHANNEL_USERNAME = "@litmouseee"
DB_PATH = os.environ.get("STATS_DB_PATH", "bot_stats.db")


def init_db():
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
                last_seen TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                event_type TEXT NOT NULL,
                payload TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)


def track_user(user):
    if user is None:
        return

    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            INSERT INTO users (
                user_id,
                username,
                first_name,
                last_name,
                first_seen,
                last_seen
            )
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                last_seen = CURRENT_TIMESTAMP
            """,
            (
                user.id,
                user.username,
                user.first_name,
                user.last_name,
            )
        )


def track_event(user_id, event_type, payload=None):
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            INSERT INTO events (user_id, event_type, payload)
            VALUES (?, ?, ?)
            """,
            (user_id, event_type, payload)
        )


def count_events(connection, event_type, payload=None):
    if payload is None:
        return connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = ?",
            (event_type,)
        ).fetchone()[0]

    return connection.execute(
        """
        SELECT COUNT(*)
        FROM events
        WHERE event_type = ? AND payload = ?
        """,
        (event_type, payload)
    ).fetchone()[0]


def get_stats():
    with sqlite3.connect(DB_PATH) as connection:
        users = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active_today = connection.execute("""
            SELECT COUNT(*)
            FROM users
            WHERE DATE(last_seen, '+3 hours') = DATE('now', '+3 hours')
        """).fetchone()[0]
        tula_users = connection.execute("""
            SELECT COUNT(DISTINCT user_id)
            FROM events
            WHERE event_type = 'guide_download' AND payload = 'tula'
        """).fetchone()[0]

        return {
            "users": users,
            "active_today": active_today,
            "starts": count_events(connection, "start"),
            "anonymous_questions": count_events(connection, "anonymous_question"),
            "anonymous_replies": count_events(connection, "anonymous_reply"),
            "guides_opened": count_events(connection, "guides_opened"),
            "tula_downloads": count_events(connection, "guide_download", "tula"),
            "tula_users": tula_users,
            "social_opened": count_events(connection, "social_opened"),
        }


def is_admin(user_id):
    return user_id in ADMIN_IDS


def main_menu():
    keyboard = [
        [InlineKeyboardButton("❓ Задать анонимный вопрос", callback_data="anon")],
        [InlineKeyboardButton("🌍 Получить гайды", callback_data="guides")],
        [InlineKeyboardButton("📱 Социальные сети", callback_data="social")]
    ]
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track_user(update.effective_user)
    track_event(update.effective_user.id, "start")

    with open("navigation.jpg", "rb") as photo:
        await update.message.reply_photo(
            photo=photo,
            caption="""это бот litmouse diary

здесь:
гайды, которые реально полезны
анон-вопросы, на которые я реально отвечаю, и все мои соцсети, если захочешь быть поближе ⭐️🫶🏼

📸 stay tuned""",
            reply_markup=main_menu()
        )


async def check_subscription(bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception:
        return False


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    track_user(query.from_user)

    if query.data.startswith("reply:"):
        if not is_admin(query.from_user.id):
            await query.message.reply_text("Эта кнопка доступна только админу.")
            return

        user_id = int(query.data.split(":", 1)[1])
        context.user_data["reply_to_user_id"] = user_id
        await query.message.reply_text(
            "Напиши ответ — бот отправит его пользователю анонимно.\n\n"
            "Чтобы отменить ответ, отправь /cancel."
        )

    elif query.data == "anon":
        track_event(query.from_user.id, "anonymous_question_started")
        context.user_data["anon_mode"] = True
        await query.message.reply_text(
            "Напиши свой вопрос — он будет отправлен анонимно."
        )

    elif query.data == "guides":
        track_event(query.from_user.id, "guides_opened")
        is_subscribed = await check_subscription(
            context.bot,
            query.from_user.id
        )

        if is_subscribed:
            keyboard = [
                [InlineKeyboardButton("📍 Тула", callback_data="city_tula")],
                [InlineKeyboardButton("🇫🇷 Париж (скоро)", callback_data="city_paris")],
                [InlineKeyboardButton("🇪🇸 Барселона (скоро)", callback_data="city_barcelona")]
            ]

            await query.message.reply_text(
                "Выберите город:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            keyboard = [
                [InlineKeyboardButton(
                    "📢 Подписаться на канал",
                    url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}"
                )],
                [InlineKeyboardButton(
                    "✅ Я подписался — проверить",
                    callback_data="guides"
                )]
            ]

            await query.message.reply_text(
                "❌ Для получения гайдов нужно подписаться на канал.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    elif query.data == "city_tula":
        track_event(query.from_user.id, "guide_download", "tula")

        await context.bot.send_document(
            chat_id=query.from_user.id,
            document="BQACAgIAAxkBAAN4ahyiUiuQiVF7dj7qZeUBm_g4wzMAAqWiAAIw2-FIbVOmu_AIVo47BA",
            caption="📍 Гайд по Туле"
        )

    elif query.data in ["city_paris", "city_barcelona"]:
        city = query.data.removeprefix("city_")
        track_event(query.from_user.id, "coming_soon_guide_clicked", city)
        await query.message.reply_text(
            "✨ Этот гайд скоро появится."
        )

    elif query.data == "social":
        track_event(query.from_user.id, "social_opened")
        keyboard = [
            [InlineKeyboardButton("Instagram", url="https://www.instagram.com/litmouse?igsh=MW5yY3FydTB3bWtzeQ==")],
            [InlineKeyboardButton("Telegram", url="https://t.me/litmouseee")],
            [InlineKeyboardButton("TikTok", url="https://www.tiktok.com/@litmouse.diary?_r=1&_t=ZS-95rWknkwcw1")]
        ]

        await query.message.reply_text(
            "Мои соцсети:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track_user(update.effective_user)

    if (
        is_admin(update.effective_user.id)
        and context.user_data.get("reply_to_user_id")
    ):
        user_id = context.user_data.pop("reply_to_user_id")

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"💌 Ответ на твой анонимный вопрос:\n\n{update.message.text}"
            )
        except Exception as error:
            await update.message.reply_text(
                f"Не получилось отправить ответ: {error}"
            )
            return

        await update.message.reply_text("Ответ отправлен ✅")
        track_event(update.effective_user.id, "anonymous_reply")
        return

    if context.user_data.get("anon_mode"):
        text = update.message.text

        keyboard = [
            [InlineKeyboardButton(
                "Ответить",
                callback_data=f"reply:{update.effective_chat.id}"
            )]
        ]

        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"❓ Анонимный вопрос:\n\n{text}",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except Exception as error:
                print(f"ADMIN MESSAGE FAILED for {admin_id}: {error}")

        context.user_data["anon_mode"] = False
        track_event(update.effective_user.id, "anonymous_question")

        await update.message.reply_text(
            "Вопрос отправлен анонимно ✅"
        )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track_user(update.effective_user)

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Эта команда доступна только админу.")
        return

    data = get_stats()
    await update.message.reply_text(
        "📊 Статистика бота\n\n"
        f"Пользователей: {data['users']}\n"
        f"Активных сегодня (МСК): {data['active_today']}\n"
        f"Запусков /start: {data['starts']}\n\n"
        f"Анонимных вопросов: {data['anonymous_questions']}\n"
        f"Ответов на вопросы: {data['anonymous_replies']}\n\n"
        f"Открытий гайдов: {data['guides_opened']}\n"
        f"Скачиваний Тулы: {data['tula_downloads']}\n"
        f"Уникальных скачавших Тулу: {data['tula_users']}\n\n"
        f"Открытий соцсетей: {data['social_opened']}"
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track_user(update.effective_user)
    context.user_data.pop("reply_to_user_id", None)
    context.user_data["anon_mode"] = False

    await update.message.reply_text("Действие отменено.")



init_db()
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("stats", stats))
app.add_handler(CommandHandler("cancel", cancel))
app.add_handler(CallbackQueryHandler(buttons))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

web = Flask(__name__)

@web.route("/")
def home():
    return "Bot is alive!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    web.run(host="0.0.0.0", port=port)

Thread(target=run_web, daemon=True).start()

async def error_handler(update, context):
    print(f"ERROR: {context.error}")

app.add_error_handler(error_handler)

print("BOT STARTING...")

try:
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )
except Exception as e:
    print(f"BOT CRASHED: {e}")
    raise
