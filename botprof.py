import os

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
ADMIN_ID = 804851530
CHANNEL_USERNAME = "@litmouseee"


def main_menu():
    keyboard = [
        [InlineKeyboardButton("❓ Задать анонимный вопрос", callback_data="anon")],
        [InlineKeyboardButton("🌍 Получить гайды", callback_data="guides")],
        [InlineKeyboardButton("📱 Социальные сети", callback_data="social")]
    ]
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    if query.data.startswith("reply:"):
        if query.from_user.id != ADMIN_ID:
            await query.message.reply_text("Эта кнопка доступна только админу.")
            return

        user_id = int(query.data.split(":", 1)[1])
        context.user_data["reply_to_user_id"] = user_id
        await query.message.reply_text(
            "Напиши ответ — бот отправит его пользователю анонимно.\n\n"
            "Чтобы отменить ответ, отправь /cancel."
        )

    elif query.data == "anon":
        context.user_data["anon_mode"] = True
        await query.message.reply_text(
            "Напиши свой вопрос — он будет отправлен анонимно."
        )

    elif query.data == "guides":
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

        await context.bot.send_document(
            chat_id=query.from_user.id,
            document="BQACAgIAAxkBAAN4ahyiUiuQiVF7dj7qZeUBm_g4wzMAAqWiAAIw2-FIbVOmu_AIVo47BA",
            caption="📍 Гайд по Туле"
        )

    elif query.data in ["city_paris", "city_barcelona"]:
        await query.message.reply_text(
            "✨ Этот гайд скоро появится."
        )

    elif query.data == "social":
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
    if (
        update.effective_user.id == ADMIN_ID
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
        return

    if context.user_data.get("anon_mode"):
        text = update.message.text

        keyboard = [
            [InlineKeyboardButton(
                "Ответить",
                callback_data=f"reply:{update.effective_chat.id}"
            )]
        ]

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"❓ Анонимный вопрос:\n\n{text}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        context.user_data["anon_mode"] = False

        await update.message.reply_text(
            "Вопрос отправлен анонимно ✅"
        )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("reply_to_user_id", None)
    context.user_data["anon_mode"] = False

    await update.message.reply_text("Действие отменено.")



app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
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
