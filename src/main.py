"""
reels-to-email — Main entry point.

Listens for messages on a Telegram bot, detects Instagram URLs,
and triggers the processing pipeline.

Run with:
    python src/main.py
"""

import os
import re
import logging
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from pipeline import run_pipeline

# Load secrets from .env at the project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Logging
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("reels-to-email")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Match Instagram links: reels, posts, IGTV, share links
INSTAGRAM_URL_PATTERN = re.compile(
    r"(https?://)?(www\.)?(instagram\.com|instagr\.am)/(p|reel|reels|tv|share)/[\w\-/?=&]+",
    re.IGNORECASE,
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply when the user sends /start."""
    welcome = (
        "👋 Hi! I'm your reels-to-email bot.\n\n"
        "Send me an Instagram Reel link (Share → Telegram → me), "
        "and I'll handle the rest:\n"
        "  • Download the video\n"
        "  • Transcribe the audio\n"
        "  • Write a full explanation (in your language)\n"
        "  • Research the topic online\n"
        "  • Tag whether it's relevant to you\n"
        "  • Email everything to you\n\n"
        "Ready when you are 🚀"
    )
    await update.message.reply_text(welcome)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle any non-command text message — detect Instagram URLs."""
    text = update.message.text or ""

    match = INSTAGRAM_URL_PATTERN.search(text)
    if not match:
        await update.message.reply_text(
            "🤔 I didn't see an Instagram link in that message.\n"
            "Send me a link to a Reel or post (instagram.com/reel/... or similar)."
        )
        return

    instagram_url = match.group(0)
    if not instagram_url.startswith("http"):
        instagram_url = "https://" + instagram_url

    logger.info(f"Received Instagram URL from chat {update.effective_chat.id}: {instagram_url}")

    await update.message.reply_text(
        "✅ Got it!\n⏳ Starting to process — I'll update you at each step."
    )

    async def status_cb(msg: str) -> None:
        try:
            await update.message.reply_text(msg)
        except Exception:
            logger.exception("Failed to send status update")

    result = await run_pipeline(instagram_url, status_callback=status_cb)

    if result["success"]:
        await update.message.reply_text(
            "📬 Email sent — check your inbox (also spam, the first time)."
        )
    else:
        await update.message.reply_text(
            f"⚠️ Processing failed.\n{result['error']}\n\n"
            "If this keeps happening, check the terminal logs for the full traceback."
        )


def main() -> None:
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "PASTE_HERE":
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing or not filled in .env. "
            "Open .env and paste the token from @BotFather."
        )

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot starting — polling for messages. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
