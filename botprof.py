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

TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_IDS = {804851530, 5242178843}
CHANNEL_USERNAME = "@litmouseee"
DB_PATH = os.environ.get("STATS_DB_PATH", "bot_stats.db")
ANON_COOLDOWN_SECONDS = 180
START_DEDUPE_SECONDS = 10

if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")


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


def should_send_start_response(user_id):
    with sqlite3.connect(DB_PATH) as connection:
        elapsed = connection.execute(
            """
            SELECT
                CAST(strftime('%s', 'now') AS INTEGER)
                - CAST(strftime('%s', MAX(created_at)) AS INTEGER)
            FROM events
            WHERE user_id = ? AND event_type = 'start_response'
            """,
            (user_id,)
        ).fetchone()[0]

        if elapsed is not None and elapsed < START_DEDUPE_SECONDS:
            return False

        connection.execute(
            """
            INSERT INTO events (user_id, event_type)
            VALUES (?, 'start_response')
            """,
            (user_id,)
        )

    return True


def get_all_user_ids():
    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute(
            "SELECT user_id FROM users ORDER BY first_seen"
        ).fetchall()

    return [row[0] for row in rows]


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
            "anonymous_dialog_replies": count_events(
                connection,
                "anonymous_dialog_reply"
            ),
            "anonymous_replies": count_events(connection, "anonymous_reply"),
            "anonymous_rate_limited": count_events(
                connection,
                "anonymous_rate_limited"
            ),
            "broadcasts": count_events(connection, "broadcast_sent"),
            "guides_opened": count_events(connection, "guides_opened"),
            "tula_downloads": count_events(connection, "guide_download", "tula"),
            "tula_users": tula_users,
            "social_opened": count_events(connection, "social_opened"),
        }


def is_admin(user_id):
    return user_id in ADMIN_IDS


def get_anon_wait_seconds(user_id):
    with sqlite3.connect(DB_PATH) as connection:
        elapsed = connection.execute(
            """
            SELECT
                CAST(strftime('%s', 'now') AS INTEGER)
                - CAST(strftime('%s', MAX(created_at)) AS INTEGER)
            FROM events
            WHERE user_id = ?
                AND event_type IN (
                    'anonymous_question',
                    'anonymous_dialog_reply'
                )
            """,
            (user_id,)
        ).fetchone()[0]

    if elapsed is None or elapsed >= ANON_COOLDOWN_SECONDS:
        return 0

    return ANON_COOLDOWN_SECONDS - elapsed


def format_wait_time(seconds):
    minutes = max(1, (seconds + 59) // 60)
    return f"{minutes} мин."


def stats_text():
    data = get_stats()
    return (
        "📊 Статистика бота\n\n"
        f"Пользователей: {data['users']}\n"
        f"Активных сегодня (МСК): {data['active_today']}\n"
        f"Запусков /start: {data['starts']}\n\n"
        f"Анонимных вопросов: {data['anonymous_questions']}\n"
        f"Продолжений диалога: {data['anonymous_dialog_replies']}\n"
        f"Ответов админов: {data['anonymous_replies']}\n"
        f"Сработок антиспама: {data['anonymous_rate_limited']}\n\n"
        f"Рассылок: {data['broadcasts']}\n\n"
        f"Открытий гайдов: {data['guides_opened']}\n"
        f"Скачиваний Тулы: {data['tula_downloads']}\n"
        f"Уникальных скачавших Тулу: {data['tula_users']}\n\n"
        f"Открытий соцсетей: {data['social_opened']}"
    )


def main_menu():
    keyboard = [
        [InlineKeyboardButton("❓ Задать анонимный вопрос", callback_data="anon")],
        [InlineKeyboardButton("🌍 Получить гайды", callback_data="guides")],
        [InlineKeyboardButton("📱 Социальные сети", callback_data="social")]
    ]
    return InlineKeyboardMarkup(keyboard)


def admin_menu():
    keyboard = [
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("📣 Рассылка", callback_data="admin_broadcast_help")],
        [InlineKeyboardButton("🛠 Команды", callback_data="admin_help")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


async def send_message_to_admins(context, user_id, text, title):
    keyboard = [
        [InlineKeyboardButton("Ответить", callback_data=f"reply:{user_id}")]
    ]
    sent_count = 0

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"{title}\n\n{text}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            sent_count += 1
        except Exception as error:
            print(f"ADMIN MESSAGE FAILED for {admin_id}: {error}")

    return sent_count


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track_user(update.effective_user)

    if not should_send_start_response(update.effective_user.id):
        track_event(update.effective_user.id, "start_duplicate_ignored")
        return

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

    elif query.data == "dialog_reply":
        track_event(query.from_user.id, "anonymous_dialog_reply_started")
        context.user_data["anon_dialog_mode"] = True
        await query.message.reply_text(
            "Напиши ответ — он уйдёт админам анонимно.\n\n"
            "Чтобы отменить, отправь /cancel."
        )

    elif query.data == "admin_stats":
        if not is_admin(query.from_user.id):
            await query.message.reply_text("Эта кнопка доступна только админу.")
            return

        await query.message.reply_text(stats_text())

    elif query.data == "admin_broadcast_help":
        if not is_admin(query.from_user.id):
            await query.message.reply_text("Эта кнопка доступна только админу.")
            return

        await query.message.reply_text(
            "📣 Рассылка\n\n"
            "Отправь команду:\n"
            "/broadcast текст сообщения\n\n"
            "Бот покажет предпросмотр и попросит подтвердить отправку."
        )

    elif query.data == "broadcast_confirm":
        if not is_admin(query.from_user.id):
            await query.message.reply_text("Эта кнопка доступна только админу.")
            return

        text = context.user_data.pop("broadcast_text", None)
        if not text:
            await query.message.reply_text(
                "Черновик рассылки не найден. Отправь /broadcast заново."
            )
            return

        users = get_all_user_ids()
        sent_count = 0
        failed_count = 0

        for user_id in users:
            try:
                await context.bot.send_message(chat_id=user_id, text=text)
                sent_count += 1
            except Exception as error:
                failed_count += 1
                print(f"BROADCAST FAILED for {user_id}: {error}")

        track_event(
            query.from_user.id,
            "broadcast_sent",
            f"sent={sent_count};failed={failed_count}"
        )

        await query.message.reply_text(
            "Рассылка завершена.\n\n"
            f"Отправлено: {sent_count}\n"
            f"Ошибок: {failed_count}"
        )

    elif query.data == "broadcast_cancel":
        if not is_admin(query.from_user.id):
            await query.message.reply_text("Эта кнопка доступна только админу.")
            return

        context.user_data.pop("broadcast_text", None)
        await query.message.reply_text("Рассылка отменена.")

    elif query.data == "admin_help":
        if not is_admin(query.from_user.id):
            await query.message.reply_text("Эта кнопка доступна только админу.")
            return

        await query.message.reply_text(
            "🛠 Админ-команды\n\n"
            "/admin — открыть админ-меню\n"
            "/stats — показать статистику\n"
            "/broadcast текст — сделать рассылку всем пользователям\n"
            "/cancel — отменить текущий ответ или ввод вопроса\n\n"
            "Чтобы ответить на анонимный вопрос, нажми кнопку "
            "«Ответить» под сообщением с вопросом."
        )

    elif query.data == "main_menu":
        await query.message.reply_text(
            "Главное меню:",
            reply_markup=main_menu()
        )

    elif query.data == "anon":
        track_event(query.from_user.id, "anonymous_question_started")
        context.user_data["anon_mode"] = True
        await query.message.reply_text(
            "Напиши свой вопрос — он будет отправлен анонимно.\n\n"
            "Чтобы отменить, отправь /cancel."
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
                text=f"💌 Ответ на твой анонимный вопрос:\n\n{update.message.text}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "Ответить анонимно",
                        callback_data="dialog_reply"
                    )]
                ])
            )
        except Exception as error:
            await update.message.reply_text(
                f"Не получилось отправить ответ: {error}"
            )
            return

        await update.message.reply_text("Ответ отправлен ✅")
        track_event(update.effective_user.id, "anonymous_reply")
        return

    if context.user_data.get("anon_dialog_mode"):
        wait_seconds = get_anon_wait_seconds(update.effective_user.id)

        if wait_seconds:
            track_event(update.effective_user.id, "anonymous_rate_limited")
            await update.message.reply_text(
                "Чтобы не было спама, анонимное сообщение можно отправлять "
                f"раз в {format_wait_time(ANON_COOLDOWN_SECONDS)}.\n\n"
                f"Попробуй ещё через {format_wait_time(wait_seconds)} "
                "или отправь /cancel."
            )
            return

        sent_count = await send_message_to_admins(
            context,
            update.effective_user.id,
            update.message.text,
            "💬 Продолжение анонимного диалога:"
        )

        if sent_count == 0:
            await update.message.reply_text(
                "Не получилось отправить сообщение админам. "
                "Попробуй позже."
            )
            return

        context.user_data["anon_dialog_mode"] = False
        track_event(update.effective_user.id, "anonymous_dialog_reply")

        await update.message.reply_text("Ответ отправлен анонимно ✅")
        return

    if context.user_data.get("anon_mode"):
        wait_seconds = get_anon_wait_seconds(update.effective_user.id)

        if wait_seconds:
            track_event(update.effective_user.id, "anonymous_rate_limited")
            await update.message.reply_text(
                "Чтобы не было спама, анонимный вопрос можно отправлять "
                f"раз в {format_wait_time(ANON_COOLDOWN_SECONDS)}.\n\n"
                f"Попробуй ещё через {format_wait_time(wait_seconds)} "
                "или отправь /cancel."
            )
            return

        sent_count = await send_message_to_admins(
            context,
            update.effective_user.id,
            update.message.text,
            "❓ Анонимный вопрос:"
        )

        if sent_count == 0:
            await update.message.reply_text(
                "Не получилось отправить вопрос админам. Попробуй позже."
            )
            return

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

    await update.message.reply_text(stats_text())


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track_user(update.effective_user)

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Эта команда доступна только админу.")
        return

    await update.message.reply_text(
        "Админ-меню:",
        reply_markup=admin_menu()
    )


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track_user(update.effective_user)

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Эта команда доступна только админу.")
        return

    text = update.message.text.partition(" ")[2].strip()
    if not text:
        await update.message.reply_text(
            "Напиши текст рассылки после команды.\n\n"
            "Пример:\n"
            "/broadcast Новый гайд уже в боте"
        )
        return

    context.user_data["broadcast_text"] = text
    users_count = len(get_all_user_ids())
    keyboard = [
        [
            InlineKeyboardButton("Отправить", callback_data="broadcast_confirm"),
            InlineKeyboardButton("Отменить", callback_data="broadcast_cancel")
        ]
    ]

    await update.message.reply_text(
        "Предпросмотр рассылки:\n\n"
        f"{text}\n\n"
        f"Получателей в базе: {users_count}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track_user(update.effective_user)
    context.user_data.pop("reply_to_user_id", None)
    context.user_data.pop("broadcast_text", None)
    context.user_data["anon_mode"] = False
    context.user_data["anon_dialog_mode"] = False

    await update.message.reply_text("Действие отменено.")



init_db()
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", admin))
app.add_handler(CommandHandler("stats", stats))
app.add_handler(CommandHandler("broadcast", broadcast))
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
