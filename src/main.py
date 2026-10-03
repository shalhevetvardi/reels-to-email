"""
reels-to-email — Main entry point.

Listens for messages on a Telegram bot, detects Instagram URLs,
and triggers the processing pipeline.

Access is fail-closed: only chat ids in ALLOWED_CHAT_IDS may trigger anything,
and even they are rate-limited. See src/authz.py.

Run with:
    python src/main.py
"""

import asyncio
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
from instagram_post import extract_user_note
from email_sender import send_alert_email
from health import interval_hours, report_failure, run_health_check

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


def _telegram_sender(bot):
    """Build a coroutine that sends a text to every allowlisted chat."""

    async def send(text: str) -> None:
        if not ALLOWED_CHAT_IDS:
            logger.warning("No allowlisted chat - the alert cannot be sent on Telegram.")
        for chat_id in sorted(ALLOWED_CHAT_IDS):
            try:
                await bot.send_message(chat_id=chat_id, text=text)
            except Exception as e:
                logger.warning("Alert to chat %s failed (%s)", chat_id, type(e).__name__)

    return send


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

    # Whatever the sender typed besides the link is passed on as a note.
    user_note = extract_user_note(text)

    result = await run_pipeline(
        instagram_url, status_callback=status_cb, language=LANGUAGE, user_note=user_note
    )

    if result["success"]:
        await update.message.reply_text(t("email_arrived", LANGUAGE))
    else:
        # Generic message only — details stay in the server log (see pipeline.py).
        await update.message.reply_text(t("failed_generic", LANGUAGE))
        # Detached: the sender already has their answer, and a slow key check must not delay it.
        context.application.create_task(
            report_failure(
                _telegram_sender(context.bot),
                send_alert_email,
                language=LANGUAGE,
                error=result.get("error", ""),
            )
        )


_FIRST_CHECK_DELAY_SECONDS = 60


async def _health_loop(application: Application) -> None:
    """Check the API keys now and then, for as long as the bot runs."""
    hours = interval_hours()
    send_tg = _telegram_sender(application.bot)
    await asyncio.sleep(_FIRST_CHECK_DELAY_SECONDS)
    while True:
        try:
            statuses = await run_health_check(send_tg, send_alert_email, language=LANGUAGE)
            counts = {}
            for s in statuses:
                counts[s.state] = counts.get(s.state, 0) + 1
            logger.info(
                "Key self-check: %d ok, %d rejected, %d missing, %d unknown",
                counts.get("ok", 0), counts.get("rejected", 0),
                counts.get("missing", 0), counts.get("unknown", 0),
            )
            for s in statuses:
                if s.state != "ok":
                    logger.info("Key self-check: %s is %s (%s)", s.name, s.state, s.detail)
        except Exception as e:
            # One bad iteration must not end the loop.
            logger.warning("Key self-check iteration failed (%s)", type(e).__name__)
        await asyncio.sleep(hours * 3600)


async def _post_init(application: Application) -> None:
    if interval_hours() > 0:
        application.bot_data["health_task"] = application.create_task(_health_loop(application))
    else:
        logger.info("Key self-check is disabled (HEALTH_CHECK_INTERVAL_HOURS=0).")


async def _post_shutdown(application: Application) -> None:
    # create_task during post_init is not tracked by PTB, so stop the loop ourselves.
    task = application.bot_data.get("health_task")
    if task is not None:
        task.cancel()


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

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot starting — polling for messages. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
