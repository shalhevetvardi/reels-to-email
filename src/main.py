"""
reels-to-email — Main entry point.

Listens for messages on a Telegram bot, detects Instagram URLs,
and triggers the processing pipeline.

Access is fail-closed: only chat ids in ALLOWED_CHAT_IDS may trigger anything,
and even they are rate-limited. See src/authz.py.

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
from profile_loader import load_language
from messages import t
from authz import parse_allowed_ids, is_authorized, RateLimiter

# Load secrets from .env at the project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=True)

# Logging
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("reels-to-email")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
LANGUAGE = load_language(default="en")

# --- Access control ---
# Only these Telegram chat ids may use the bot. Unset/empty => reject everyone
# (fail-closed): the repo is public and the pipeline costs money, so a missing
# allowlist must never mean "open to all".
ALLOWED_CHAT_IDS = parse_allowed_ids(os.getenv("ALLOWED_CHAT_IDS"))

# Backstop cap even for an allowed sender (compromised account / accidental loop).
_RATE_LIMIT_MAX_PER_HOUR = int(os.getenv("RATE_LIMIT_MAX_PER_HOUR", "10"))
rate_limiter = RateLimiter(max_per_window=_RATE_LIMIT_MAX_PER_HOUR, window_seconds=3600)

# Match Instagram links: reels, posts, IGTV, share links
INSTAGRAM_URL_PATTERN = re.compile(
    r"(https?://)?(www\.)?(instagram\.com|instagr\.am)/(p|reel|reels|tv|share)/[\w\-/?=&]+",
    re.IGNORECASE,
)


def _authorized(update: Update) -> bool:
    """Return True only for an allowlisted chat. Logs and denies everyone else.

    Denial is silent to the sender (no reply): the repo is public, so we give
    an unknown prober neither a cost channel nor confirmation the bot exists.
    """
    chat_id = update.effective_chat.id if update.effective_chat else None
    if is_authorized(chat_id, ALLOWED_CHAT_IDS):
        return True
    logger.warning("Rejected message from unauthorized chat %s", chat_id)
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply when the user sends /start."""
    if not _authorized(update):
        return
    await update.message.reply_text(t("welcome", LANGUAGE))


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle any non-command text message — detect Instagram URLs."""
    if not _authorized(update):
        return

    text = update.message.text or ""

    match = INSTAGRAM_URL_PATTERN.search(text)
    if not match:
        await update.message.reply_text(t("not_instagram_url", LANGUAGE))
        return

    instagram_url = match.group(0)
    if not instagram_url.startswith("http"):
        instagram_url = "https://" + instagram_url

    # Rate-limit the paid pipeline (only counts real Instagram triggers).
    chat_id = update.effective_chat.id
    if not rate_limiter.allow(chat_id):
        logger.warning("Rate limit reached for chat %s", chat_id)
        await update.message.reply_text(t("rate_limited", LANGUAGE))
        return

    logger.info(f"Received Instagram URL from chat {chat_id}: {instagram_url}")

    await update.message.reply_text(t("got_it", LANGUAGE))

    async def status_cb(msg: str) -> None:
        try:
            await update.message.reply_text(msg)
        except Exception:
            logger.exception("Failed to send status update")

    result = await run_pipeline(instagram_url, status_callback=status_cb, language=LANGUAGE)

    if result["success"]:
        await update.message.reply_text(t("email_arrived", LANGUAGE))
    else:
        # Generic message only — details stay in the server log (see pipeline.py).
        await update.message.reply_text(t("failed_generic", LANGUAGE))


def main() -> None:
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "PASTE_HERE":
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing or not filled in .env. "
            "Open .env and paste the token from @BotFather."
        )

    if not ALLOWED_CHAT_IDS:
        logger.warning(
            "ALLOWED_CHAT_IDS is not set — the bot will reject ALL messages "
            "(fail-closed). Set ALLOWED_CHAT_IDS to your Telegram chat id(s) "
            "so you can use the bot."
        )
    else:
        logger.info("Allowlist active for %d chat id(s).", len(ALLOWED_CHAT_IDS))

    logger.info(f"Interface language: {LANGUAGE}")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot starting — polling for messages. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
