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
DEFAULT_DB_PATH = "/var/data/bot_stats.db"
DB_PATH = os.environ.get(
    "STATS_DB_PATH",
    DEFAULT_DB_PATH if os.path.isdir("/var/data") else "bot_stats.db"
)
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
WEBHOOK_PATH = os.environ.get("WEBHOOK_PATH", "telegram")
ANON_COOLDOWN_SECONDS = 180
START_DEDUPE_SECONDS = 10
CALLBACK_DEDUPE_SECONDS = 5
TULA_FILE_ID = "BQACAgIAAxkBAAN4ahyiUiuQiVF7dj7qZeUBm_g4wzMAAqWiAAIw2-FIbVOmu_AIVo47BA"

if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")


def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

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
        connection.execute("""
            CREATE TABLE IF NOT EXISTS guides (
                city_key TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                file_id TEXT,
                is_available INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 100,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS guide_waitlist (
                user_id INTEGER NOT NULL,
                city_key TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, city_key)
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                rating INTEGER NOT NULL,
                review_text TEXT NOT NULL,
                show_username INTEGER NOT NULL DEFAULT 0,
                display_name TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS anon_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                question_text TEXT NOT NULL,
                answer_text TEXT,
                channel_message_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                published_at TEXT
            )
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_user_type_payload_created
            ON events (user_id, event_type, payload, created_at)
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_type_created
            ON events (event_type, created_at)
        """)
        seed_guides(connection)


def seed_guides(connection):
    default_guides = [
        ("tula", "📍 Тула", TULA_FILE_ID, 1, 10),
        ("paris", "🇫🇷 Париж", None, 0, 20),
        ("barcelona", "🇪🇸 Барселона", None, 0, 30),
    ]

    for city_key, title, file_id, is_available, sort_order in default_guides:
        connection.execute(
            """
            INSERT INTO guides (
                city_key,
                title,
                file_id,
                is_available,
                sort_order
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(city_key) DO NOTHING
            """,
            (city_key, title, file_id, is_available, sort_order)
        )


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


def should_handle_callback(user_id, callback_data):
    with sqlite3.connect(DB_PATH) as connection:
        elapsed = connection.execute(
            """
            SELECT
                CAST(strftime('%s', 'now') AS INTEGER)
                - CAST(strftime('%s', MAX(created_at)) AS INTEGER)
            FROM events
            WHERE user_id = ?
                AND event_type = 'callback_handled'
                AND payload = ?
            """,
            (user_id, callback_data)
        ).fetchone()[0]

        if elapsed is not None and elapsed < CALLBACK_DEDUPE_SECONDS:
            return False

        connection.execute(
            """
            INSERT INTO events (user_id, event_type, payload)
            VALUES (?, 'callback_handled', ?)
            """,
            (user_id, callback_data)
        )

    return True


def get_all_user_ids():
    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute(
            "SELECT user_id FROM users ORDER BY first_seen"
        ).fetchall()

    return [row[0] for row in rows]


def get_guides():
    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute(
            """
            SELECT city_key, title, file_id, is_available
            FROM guides
            ORDER BY sort_order, title
            """
        ).fetchall()

    return [
        {
            "city_key": row[0],
            "title": row[1],
            "file_id": row[2],
            "is_available": bool(row[3]),
        }
        for row in rows
    ]


def get_guide(city_key):
    with sqlite3.connect(DB_PATH) as connection:
        row = connection.execute(
            """
            SELECT city_key, title, file_id, is_available
            FROM guides
            WHERE city_key = ?
            """,
            (city_key,)
        ).fetchone()

    if row is None:
        return None

    return {
        "city_key": row[0],
        "title": row[1],
        "file_id": row[2],
        "is_available": bool(row[3]),
    }


def save_guide(city_key, title, file_id):
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            INSERT INTO guides (
                city_key,
                title,
                file_id,
                is_available,
                updated_at
            )
            VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(city_key) DO UPDATE SET
                title = excluded.title,
                file_id = excluded.file_id,
                is_available = 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (city_key, title, file_id)
        )


def add_to_guide_waitlist(user_id, city_key):
    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO guide_waitlist (user_id, city_key)
            VALUES (?, ?)
            """,
            (user_id, city_key)
        )

    return cursor.rowcount > 0


def get_guide_waitlist_user_ids(city_key):
    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute(
            """
            SELECT user_id
            FROM guide_waitlist
            WHERE city_key = ?
            ORDER BY created_at
            """,
            (city_key,)
        ).fetchall()

    return [row[0] for row in rows]


def clear_guide_waitlist(city_key):
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            "DELETE FROM guide_waitlist WHERE city_key = ?",
            (city_key,)
        )


def get_waitlist_summary():
    with sqlite3.connect(DB_PATH) as connection:
        total = connection.execute(
            "SELECT COUNT(*) FROM guide_waitlist"
        ).fetchone()[0]
        rows = connection.execute(
            """
            SELECT guides.title, COUNT(guide_waitlist.user_id)
            FROM guide_waitlist
            LEFT JOIN guides ON guides.city_key = guide_waitlist.city_key
            GROUP BY guide_waitlist.city_key
            ORDER BY COUNT(guide_waitlist.user_id) DESC
            """
        ).fetchall()

    return total, rows


def recent_questions(limit=10):
    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute(
            """
            SELECT event_type, payload, created_at
            FROM events
            WHERE event_type IN (
                'anonymous_question',
                'anonymous_dialog_reply'
            )
                AND payload IS NOT NULL
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()

    return rows


def save_anon_question(user_id, question_text):
    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.execute(
            """
            INSERT INTO anon_questions (user_id, question_text)
            VALUES (?, ?)
            """,
            (user_id, question_text)
        )
        return cursor.lastrowid


def get_anon_question(question_id):
    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT id, user_id, question_text, channel_message_id
            FROM anon_questions
            WHERE id = ?
            """,
            (question_id,)
        ).fetchone()

    return dict(row) if row else None


def mark_anon_question_published(question_id, answer_text, channel_message_id):
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            UPDATE anon_questions
            SET answer_text = ?,
                channel_message_id = ?,
                published_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (answer_text, channel_message_id, question_id)
        )


def save_feedback(user_id, rating, review_text, show_username, display_name=None):
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            INSERT INTO feedback (
                user_id,
                rating,
                review_text,
                show_username,
                display_name
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                rating,
                review_text,
                int(show_username),
                display_name if show_username else None,
            )
        )


def get_feedback_stats():
    with sqlite3.connect(DB_PATH) as connection:
        row = connection.execute(
            """
            SELECT COUNT(*), AVG(rating)
            FROM feedback
            """
        ).fetchone()
        rating_rows = connection.execute(
            """
            SELECT rating, COUNT(*)
            FROM feedback
            GROUP BY rating
            ORDER BY rating DESC
            """
        ).fetchall()

    total = row[0] or 0
    average = row[1] or 0
    distribution = {rating: count for rating, count in rating_rows}
    return total, average, distribution


def recent_feedback(limit=10):
    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute(
            """
            SELECT rating, review_text, show_username, display_name, created_at
            FROM feedback
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()

    return rows


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
    waitlist_total, waitlist_rows = get_waitlist_summary()
    feedback_total, feedback_average, _ = get_feedback_stats()
    waitlist_lines = [
        f"{title or 'Без названия'}: {count}"
        for title, count in waitlist_rows
    ]
    waitlist_text = "\n".join(waitlist_lines) if waitlist_lines else "нет"

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
        f"Ожидают будущие гайды: {waitlist_total}\n"
        f"{waitlist_text}\n\n"
        f"Отзывов: {feedback_total}\n"
        f"Средняя оценка: {feedback_average:.2f}/5\n\n"
        f"Открытий соцсетей: {data['social_opened']}"
    )


def questions_text():
    rows = recent_questions()

    if not rows:
        return "Пока нет сохранённых анонимных вопросов."

    lines = ["Последние анонимные сообщения:"]

    for index, (event_type, payload, created_at) in enumerate(rows, start=1):
        title = "Вопрос"
        if event_type == "anonymous_dialog_reply":
            title = "Продолжение"

        text = payload.strip()
        if len(text) > 300:
            text = f"{text[:300]}..."

        lines.append(f"\n{index}. {title} · {created_at}\n{text}")

    return "\n".join(lines)


def feedback_text():
    total, average, distribution = get_feedback_stats()
    rows = recent_feedback()

    if total == 0:
        return "Пока нет отзывов."

    lines = [
        "⭐ Отзывы о боте\n",
        f"Всего отзывов: {total}",
        f"Средняя оценка: {average:.2f}/5",
        "",
        "Оценки:"
    ]

    for rating in range(5, 0, -1):
        lines.append(f"{rating}⭐: {distribution.get(rating, 0)}")

    lines.append("\nПоследние отзывы:")

    for index, (rating, review_text, show_username, display_name, created_at) in enumerate(
        rows,
        start=1
    ):
        author = display_name if show_username and display_name else "анонимно"
        text = review_text.strip()
        if len(text) > 300:
            text = f"{text[:300]}..."

        lines.append(
            f"\n{index}. {rating}⭐ · {author} · {created_at}\n{text}"
        )

    return "\n".join(lines)


def user_display_name(user):
    if user.username:
        return f"@{user.username}"

    name_parts = [user.first_name, user.last_name]
    name = " ".join(part for part in name_parts if part)
    return name or "пользователь Telegram"


def main_menu():
    keyboard = [
        [InlineKeyboardButton("❓ Задать анонимный вопрос", callback_data="anon")],
        [InlineKeyboardButton("🌍 Получить гайды", callback_data="guides")],
        [InlineKeyboardButton("⭐ Оставить отзыв", callback_data="feedback_start")],
        [InlineKeyboardButton("📱 Социальные сети", callback_data="social")]
    ]
    return InlineKeyboardMarkup(keyboard)


def admin_menu():
    keyboard = [
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("🗂 Гайды", callback_data="admin_guides_help")],
        [InlineKeyboardButton("❓ Вопросы", callback_data="admin_questions")],
        [InlineKeyboardButton("⭐ Отзывы", callback_data="admin_feedback")],
        [InlineKeyboardButton("📣 Рассылка", callback_data="admin_broadcast_help")],
        [InlineKeyboardButton("🛠 Команды", callback_data="admin_help")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def guides_menu():
    keyboard = []

    for guide in get_guides():
        title = guide["title"]
        if not guide["is_available"]:
            title = f"{title} (скоро)"

        keyboard.append([
            InlineKeyboardButton(
                title,
                callback_data=f"city_{guide['city_key']}"
            )
        ])

    return InlineKeyboardMarkup(keyboard)


def feedback_rating_menu():
    keyboard = [
        [
            InlineKeyboardButton(f"{rating}⭐", callback_data=f"feedback_rating:{rating}")
            for rating in range(1, 6)
        ],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def feedback_privacy_menu():
    keyboard = [
        [InlineKeyboardButton("Анонимно", callback_data="feedback_privacy:anon")],
        [InlineKeyboardButton("Оставить ник", callback_data="feedback_privacy:nick")]
    ]
    return InlineKeyboardMarkup(keyboard)


def channel_post_text(question_text, answer_text):
    return (
        "❓ Анонимный вопрос:\n\n"
        f"{question_text}\n\n"
        "💬 Ответ:\n\n"
        f"{answer_text}"
    )


def channel_post_link(channel_message_id):
    return f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}/{channel_message_id}"


def ask_question_keyboard(bot_username):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "❓ Задать свой вопрос анонимно",
            url=f"https://t.me/{bot_username}?start=ask"
        )]
    ])


def publish_preview_menu():
    keyboard = [
        [InlineKeyboardButton("📣 Опубликовать в канал", callback_data="publish_confirm")],
        [InlineKeyboardButton("✉️ Только ответить лично", callback_data="publish_private")],
        [InlineKeyboardButton("✖️ Отмена", callback_data="publish_cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)


async def send_anon_answer(context, user_id, answer_text):
    await context.bot.send_message(
        chat_id=user_id,
        text=f"💌 Ответ на твой анонимный вопрос:\n\n{answer_text}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "Ответить анонимно",
                callback_data="dialog_reply"
            )]
        ])
    )


async def send_message_to_admins(context, user_id, text, title, question_id=None):
    keyboard = [
        [InlineKeyboardButton("Ответить", callback_data=f"reply:{user_id}")]
    ]
    if question_id:
        keyboard.append([
            InlineKeyboardButton(
                "📣 Ответить и опубликовать",
                callback_data=f"reply_pub:{question_id}"
            )
        ])
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

    if context.args and context.args[0] == "ask":
        track_event(update.effective_user.id, "anonymous_question_started", "channel_link")
        context.user_data["anon_mode"] = True
        await update.message.reply_text(
            "Напиши свой вопрос — он будет отправлен анонимно.\n\n"
            "Чтобы отменить, отправь /cancel."
        )
        return

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

    if not should_handle_callback(query.from_user.id, query.data):
        track_event(query.from_user.id, "callback_duplicate_ignored", query.data)
        return

    if query.data.startswith("reply:"):
        if not is_admin(query.from_user.id):
            await query.message.reply_text("Эта кнопка доступна только админу.")
            return

        user_id = int(query.data.split(":", 1)[1])
        context.user_data.pop("publish_question_id", None)
        context.user_data["reply_to_user_id"] = user_id
        await query.message.reply_text(
            "Напиши ответ — бот отправит его пользователю анонимно.\n\n"
            "Чтобы отменить ответ, отправь /cancel."
        )

    elif query.data.startswith("reply_pub:"):
        if not is_admin(query.from_user.id):
            await query.message.reply_text("Эта кнопка доступна только админу.")
            return

        question_id = int(query.data.split(":", 1)[1])
        question = get_anon_question(question_id)

        if not question:
            await query.message.reply_text("Вопрос не найден в базе.")
            return

        if question["channel_message_id"]:
            await query.message.reply_text(
                "Этот вопрос уже опубликован:\n"
                f"{channel_post_link(question['channel_message_id'])}"
            )
            return

        context.user_data.pop("reply_to_user_id", None)
        context.user_data.pop("pending_publish", None)
        context.user_data["publish_question_id"] = question_id
        await query.message.reply_text(
            "Напиши ответ. Перед публикацией я покажу, как будет выглядеть пост.\n\n"
            "Чтобы отменить, отправь /cancel."
        )

    elif query.data in ("publish_confirm", "publish_private", "publish_cancel"):
        if not is_admin(query.from_user.id):
            await query.message.reply_text("Эта кнопка доступна только админу.")
            return

        pending = context.user_data.pop("pending_publish", None)
        await query.edit_message_reply_markup(reply_markup=None)

        if not pending:
            await query.message.reply_text("Нет ответа, ожидающего публикации.")
            return

        if query.data == "publish_cancel":
            await query.message.reply_text("Публикация отменена. Ответ не отправлен.")
            return

        question = get_anon_question(pending["question_id"])
        if not question:
            await query.message.reply_text("Вопрос не найден в базе.")
            return

        if question["channel_message_id"]:
            await query.message.reply_text(
                "Этот вопрос уже опубликован другим админом:\n"
                f"{channel_post_link(question['channel_message_id'])}"
            )
            return

        channel_message = None
        if query.data == "publish_confirm":
            try:
                channel_message = await context.bot.send_message(
                    chat_id=CHANNEL_USERNAME,
                    text=channel_post_text(
                        question["question_text"],
                        pending["answer_text"]
                    ),
                    reply_markup=ask_question_keyboard(context.bot.username)
                )
            except Exception as error:
                context.user_data["pending_publish"] = pending
                await query.message.reply_text(
                    f"Не получилось опубликовать в {CHANNEL_USERNAME}: {error}\n\n"
                    "Проверь, что бот — админ канала с правом публикации.",
                    reply_markup=publish_preview_menu()
                )
                return

            mark_anon_question_published(
                question["id"],
                pending["answer_text"],
                channel_message.message_id
            )
            track_event(query.from_user.id, "anonymous_answer_published", str(question["id"]))

        try:
            await send_anon_answer(context, question["user_id"], pending["answer_text"])
            if channel_message:
                await context.bot.send_message(
                    chat_id=question["user_id"],
                    text=(
                        "📣 Твой вопрос попал в канал — анонимно, без имени:\n"
                        f"{channel_post_link(channel_message.message_id)}"
                    )
                )
        except Exception as error:
            await query.message.reply_text(
                f"Не получилось отправить ответ пользователю: {error}"
            )
        else:
            track_event(query.from_user.id, "anonymous_reply")

        if channel_message:
            await query.message.reply_text(
                "Опубликовано ✅\n"
                f"{channel_post_link(channel_message.message_id)}"
            )
        else:
            await query.message.reply_text("Ответ отправлен лично ✅")

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

    elif query.data == "admin_guides_help":
        if not is_admin(query.from_user.id):
            await query.message.reply_text("Эта кнопка доступна только админу.")
            return

        await query.message.reply_text(
            "🗂 Управление гайдами\n\n"
            "Чтобы добавить или заменить гайд:\n"
            "/addguide city_key Название города\n\n"
            "Пример:\n"
            "/addguide paris 🇫🇷 Париж\n\n"
            "После команды отправь PDF или другой файл следующим сообщением."
        )

    elif query.data == "admin_questions":
        if not is_admin(query.from_user.id):
            await query.message.reply_text("Эта кнопка доступна только админу.")
            return

        await query.message.reply_text(questions_text())

    elif query.data == "admin_feedback":
        if not is_admin(query.from_user.id):
            await query.message.reply_text("Эта кнопка доступна только админу.")
            return

        await query.message.reply_text(feedback_text())

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
            "/addguide city_key Название — добавить или заменить гайд\n"
            "/questions — последние анонимные вопросы\n"
            "/feedback — отзывы и средняя оценка\n"
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

    elif query.data == "feedback_start":
        track_event(query.from_user.id, "feedback_started")
        context.user_data["pending_feedback"] = {}
        context.user_data["feedback_text_mode"] = False
        await query.message.reply_text(
            "Спасибо, что хочешь оставить отзыв.\n\n"
            "Сначала поставь оценку боту:",
            reply_markup=feedback_rating_menu()
        )

    elif query.data.startswith("feedback_rating:"):
        rating = int(query.data.split(":", 1)[1])
        context.user_data["pending_feedback"] = {"rating": rating}
        context.user_data["feedback_text_mode"] = True
        track_event(query.from_user.id, "feedback_rating_selected", str(rating))
        await query.message.reply_text(
            f"Оценка: {rating}⭐\n\n"
            "Теперь напиши отзыв одним сообщением.\n\n"
            "По умолчанию он будет анонимным. После текста можно будет "
            "выбрать, оставить ник или нет."
        )

    elif query.data.startswith("feedback_privacy:"):
        pending_feedback = context.user_data.get("pending_feedback")
        if not pending_feedback or not pending_feedback.get("review_text"):
            await query.message.reply_text(
                "Черновик отзыва не найден. Начни заново через кнопку «Оставить отзыв»."
            )
            return

        privacy = query.data.split(":", 1)[1]
        show_username = privacy == "nick"
        display_name = user_display_name(query.from_user) if show_username else None
        rating = pending_feedback["rating"]
        review_text = pending_feedback["review_text"]

        save_feedback(
            query.from_user.id,
            rating,
            review_text,
            show_username,
            display_name
        )
        track_event(
            query.from_user.id,
            "feedback_submitted",
            f"rating={rating};show_username={int(show_username)}"
        )
        context.user_data.pop("pending_feedback", None)
        context.user_data["feedback_text_mode"] = False

        author = display_name if show_username else "анонимно"
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        "⭐ Новый отзыв о боте\n\n"
                        f"Оценка: {rating}/5\n"
                        f"Автор: {author}\n\n"
                        f"{review_text}"
                    )
                )
            except Exception as error:
                print(f"FEEDBACK ADMIN MESSAGE FAILED for {admin_id}: {error}")

        await query.message.reply_text(
            "Спасибо, отзыв сохранён ✅",
            reply_markup=main_menu()
        )

    elif query.data == "guides":
        track_event(query.from_user.id, "guides_opened")
        is_subscribed = await check_subscription(
            context.bot,
            query.from_user.id
        )

        if is_subscribed:
            await query.message.reply_text(
                "Выберите город:",
                reply_markup=guides_menu()
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

    elif query.data.startswith("city_"):
        city_key = query.data.removeprefix("city_")
        guide = get_guide(city_key)

        if not guide:
            await query.message.reply_text("Этот гайд не найден.")
            return

        if not guide["is_available"] or not guide["file_id"]:
            track_event(query.from_user.id, "coming_soon_guide_clicked", city_key)
            keyboard = [
                [InlineKeyboardButton(
                    "🔔 Сообщить о выходе",
                    callback_data=f"waitlist:{city_key}"
                )],
                [InlineKeyboardButton("🌍 Все гайды", callback_data="guides")]
            ]

            await query.message.reply_text(
                f"✨ {guide['title']} скоро появится.\n\n"
                "Можно нажать кнопку ниже — бот напишет тебе, когда гайд выйдет.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        track_event(query.from_user.id, "guide_download", city_key)

        await context.bot.send_document(
            chat_id=query.from_user.id,
            document=guide["file_id"],
            caption=f"{guide['title']}"
        )

    elif query.data.startswith("waitlist:"):
        city_key = query.data.split(":", 1)[1]
        guide = get_guide(city_key)

        if not guide:
            await query.message.reply_text("Этот гайд не найден.")
            return

        is_new = add_to_guide_waitlist(query.from_user.id, city_key)
        track_event(query.from_user.id, "guide_waitlist_joined", city_key)

        if is_new:
            await query.message.reply_text(
                f"Готово, я напишу тебе, когда выйдет {guide['title']} ✅"
            )
        else:
            await query.message.reply_text(
                f"Ты уже в списке ожидания: {guide['title']} ✅"
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
            await send_anon_answer(context, user_id, update.message.text)
        except Exception as error:
            await update.message.reply_text(
                f"Не получилось отправить ответ: {error}"
            )
            return

        await update.message.reply_text("Ответ отправлен ✅")
        track_event(update.effective_user.id, "anonymous_reply")
        return

    if (
        is_admin(update.effective_user.id)
        and context.user_data.get("publish_question_id")
    ):
        question_id = context.user_data.pop("publish_question_id")
        question = get_anon_question(question_id)

        if not question:
            await update.message.reply_text("Вопрос не найден в базе.")
            return

        post_text = channel_post_text(question["question_text"], update.message.text)
        if len(post_text) > 4000:
            context.user_data["publish_question_id"] = question_id
            await update.message.reply_text(
                "Пост получается длиннее лимита Telegram (4096 символов). "
                "Сократи ответ и отправь ещё раз или /cancel."
            )
            return

        context.user_data["pending_publish"] = {
            "question_id": question_id,
            "answer_text": update.message.text,
        }
        await update.message.reply_text(
            "Так пост будет выглядеть в канале:\n\n"
            "———\n"
            f"{post_text}\n"
            "———\n\n"
            "Под постом будет кнопка «❓ Задать свой вопрос анонимно». "
            "Пользователь получит ответ лично и ссылку на пост.",
            reply_markup=publish_preview_menu()
        )
        return

    if context.user_data.get("feedback_text_mode"):
        pending_feedback = context.user_data.get("pending_feedback")
        if not pending_feedback or not pending_feedback.get("rating"):
            context.user_data["feedback_text_mode"] = False
            await update.message.reply_text(
                "Оценка не найдена. Начни заново через кнопку «Оставить отзыв»."
            )
            return

        review_text = update.message.text.strip()
        if not review_text:
            await update.message.reply_text(
                "Отзыв не может быть пустым. Напиши пару слов и отправь ещё раз."
            )
            return

        if len(review_text) > 1200:
            await update.message.reply_text(
                "Отзыв получился длиннее 1200 символов. "
                "Сократи его, пожалуйста, и отправь ещё раз."
            )
            return

        pending_feedback["review_text"] = review_text
        context.user_data["feedback_text_mode"] = False
        track_event(update.effective_user.id, "feedback_text_added")
        await update.message.reply_text(
            "Как сохранить отзыв?",
            reply_markup=feedback_privacy_menu()
        )
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
        track_event(
            update.effective_user.id,
            "anonymous_dialog_reply",
            update.message.text
        )

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

        question_id = save_anon_question(
            update.effective_user.id,
            update.message.text
        )
        sent_count = await send_message_to_admins(
            context,
            update.effective_user.id,
            update.message.text,
            "❓ Анонимный вопрос:",
            question_id=question_id
        )

        if sent_count == 0:
            await update.message.reply_text(
                "Не получилось отправить вопрос админам. Попробуй позже."
            )
            return

        context.user_data["anon_mode"] = False
        track_event(
            update.effective_user.id,
            "anonymous_question",
            update.message.text
        )

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


async def add_guide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track_user(update.effective_user)

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Эта команда доступна только админу.")
        return

    _, _, rest = update.message.text.partition(" ")
    parts = rest.strip().split(maxsplit=1)

    if len(parts) != 2:
        await update.message.reply_text(
            "Используй формат:\n"
            "/addguide city_key Название города\n\n"
            "Пример:\n"
            "/addguide paris 🇫🇷 Париж"
        )
        return

    city_key, title = parts
    city_key = city_key.lower().strip()

    if (
        len(city_key) > 40
        or not city_key.replace("_", "").replace("-", "").isalnum()
    ):
        await update.message.reply_text(
            "city_key должен быть до 40 символов и состоять из "
            "латинских букв, цифр, дефиса или подчёркивания."
        )
        return

    context.user_data["pending_guide"] = {
        "city_key": city_key,
        "title": title.strip(),
    }

    await update.message.reply_text(
        f"Теперь отправь файл для гайда: {title.strip()}.\n\n"
        "Чтобы отменить, отправь /cancel."
    )


async def questions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track_user(update.effective_user)

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Эта команда доступна только админу.")
        return

    await update.message.reply_text(questions_text())


async def feedback_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track_user(update.effective_user)

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Эта команда доступна только админу.")
        return

    await update.message.reply_text(feedback_text())


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track_user(update.effective_user)

    if not is_admin(update.effective_user.id):
        return

    pending_guide = context.user_data.get("pending_guide")
    if not pending_guide:
        await update.message.reply_text(
            "Если это файл гайда, сначала отправь:\n"
            "/addguide city_key Название города"
        )
        return

    document = update.message.document
    save_guide(
        pending_guide["city_key"],
        pending_guide["title"],
        document.file_id
    )
    context.user_data.pop("pending_guide", None)
    track_event(
        update.effective_user.id,
        "guide_saved",
        pending_guide["city_key"]
    )

    waitlist_user_ids = get_guide_waitlist_user_ids(pending_guide["city_key"])
    notified_count = 0
    failed_count = 0

    for user_id in waitlist_user_ids:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"✨ Гайд {pending_guide['title']} уже в боте!\n\n"
                    "Открой раздел «Получить гайды» и забирай его."
                ),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🌍 Получить гайды", callback_data="guides")]
                ])
            )
            notified_count += 1
        except Exception as error:
            failed_count += 1
            print(f"GUIDE WAITLIST NOTIFY FAILED for {user_id}: {error}")

    if waitlist_user_ids:
        clear_guide_waitlist(pending_guide["city_key"])
        track_event(
            update.effective_user.id,
            "guide_waitlist_notified",
            (
                f"{pending_guide['city_key']};"
                f"sent={notified_count};failed={failed_count}"
            )
        )

    await update.message.reply_text(
        "Гайд сохранён ✅\n\n"
        f"Город: {pending_guide['title']}\n"
        f"Ключ: {pending_guide['city_key']}\n"
        f"Уведомлено ожидающих: {notified_count}\n"
        f"Ошибок уведомления: {failed_count}"
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track_user(update.effective_user)
    context.user_data.pop("reply_to_user_id", None)
    context.user_data.pop("publish_question_id", None)
    context.user_data.pop("pending_publish", None)
    context.user_data.pop("broadcast_text", None)
    context.user_data.pop("pending_guide", None)
    context.user_data.pop("pending_feedback", None)
    context.user_data["feedback_text_mode"] = False
    context.user_data["anon_mode"] = False
    context.user_data["anon_dialog_mode"] = False

    await update.message.reply_text("Действие отменено.")



init_db()
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", admin))
app.add_handler(CommandHandler("stats", stats))
app.add_handler(CommandHandler("broadcast", broadcast))
app.add_handler(CommandHandler("addguide", add_guide))
app.add_handler(CommandHandler("questions", questions))
app.add_handler(CommandHandler("feedback", feedback_report))
app.add_handler(CommandHandler("cancel", cancel))
app.add_handler(CallbackQueryHandler(buttons))
app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

web = Flask(__name__)


@web.route("/")
def home():
    return "Bot is alive!"


def run_web():
    port = int(os.environ.get("PORT", 10000))
    web.run(host="0.0.0.0", port=port)


async def error_handler(update, context):
    print(f"ERROR: {context.error}")

app.add_error_handler(error_handler)

print("BOT STARTING...")

try:
    if WEBHOOK_URL:
        port = int(os.environ.get("PORT", 10000))
        webhook_url = f"{WEBHOOK_URL.rstrip('/')}/{WEBHOOK_PATH}"
        print(f"WEBHOOK MODE: {webhook_url}")

        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=WEBHOOK_PATH,
            webhook_url=webhook_url,
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        )
    else:
        print("POLLING MODE")
        Thread(target=run_web, daemon=True).start()
        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        )
except Exception as e:
    print(f"BOT CRASHED: {e}")
    raise
