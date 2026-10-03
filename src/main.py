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
import signal
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from telegram import LinkPreviewOptions, ReplyParameters, Update
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

# --- Graceful stop ---
# A redeploy sends SIGTERM and then, after railway.json's drainingSeconds, SIGKILL. In that
# window python-telegram-bot finishes the update being handled and the ones queued behind it;
# what it does not do is tell the sender, so we track what is in flight and say so.


@dataclass(eq=False)
class _InFlight:
    chat_id: int
    message_id: int | None
    url: str


_in_flight: list[_InFlight] = []
_draining = False
# A task nobody references can be garbage collected before it finishes.
_drain_task: asyncio.Task | None = None

_NOTICE_TIMEOUT_SECONDS = 10
_STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGABRT)


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
        # Name the skipped link in the log and in a reply that quotes the rejected message:
        # several links sent together are refused together, and otherwise nobody can tell
        # afterwards which ones to send again.
        logger.warning("Rate limit reached for chat %s, skipped %s", chat_id, instagram_url)
        await update.message.reply_text(
            t("rate_limited", LANGUAGE, url=instagram_url),
            do_quote=True,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        return

    logger.info(f"Received Instagram URL from chat {chat_id}: {instagram_url}")

    entry = _InFlight(chat_id, getattr(update.message, "message_id", None), instagram_url)
    _in_flight.append(entry)
    try:
        if _draining:
            await update.message.reply_text(
                t("got_it_draining", LANGUAGE, url=instagram_url),
                do_quote=True,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        else:
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
    finally:
        _in_flight.remove(entry)


async def _send_update_notice(application: Application, entry: _InFlight) -> None:
    """Tell one sender their link is still being processed. Never raises."""
    reply = (
        ReplyParameters(message_id=entry.message_id, allow_sending_without_reply=True)
        if entry.message_id is not None
        else None
    )
    try:
        await asyncio.wait_for(
            application.bot.send_message(
                chat_id=entry.chat_id,
                text=t("update_in_progress", LANGUAGE, url=entry.url),
                reply_parameters=reply,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            ),
            timeout=_NOTICE_TIMEOUT_SECONDS,
        )
    except Exception as e:
        # Type only: the text of a Telegram error can carry the request details.
        logger.warning("Update notice was not delivered (%s)", type(e).__name__)


def _end_start_up() -> None:
    raise SystemExit


async def _announce_and_stop(application: Application, entries: list[_InFlight]) -> None:
    try:
        for entry in entries:
            # It may have finished while earlier notices were being sent.
            if entry in _in_flight:
                await _send_update_notice(application, entry)
    finally:
        application.stop_running()
        if not application.running:
            # Still starting: stop_running() then only sets a flag the library reads once, right
            # after post_init, so a signal during start-up would be ignored. SystemExit is what
            # the library's own stop handler raises to end a start-up. As a loop callback, like
            # the library's, so that it ends the loop instead of failing a task.
            asyncio.get_running_loop().call_soon(_end_start_up)


def _on_stop_signal(application: Application, signum: int) -> None:
    global _draining, _drain_task
    name = signal.Signals(signum).name
    if _draining:
        logger.info("Stop signal %s received again - already draining.", name)
        return
    _draining = True
    entries = list(_in_flight)
    logger.info("Stop signal %s received - %d link(s) in flight.", name, len(entries))
    _drain_task = asyncio.get_running_loop().create_task(_announce_and_stop(application, entries))


def _install_stop_handlers(application: Application) -> None:
    loop = asyncio.get_running_loop()
    try:
        for sig in _STOP_SIGNALS:
            loop.add_signal_handler(sig, _on_stop_signal, application, sig)
    except (NotImplementedError, RuntimeError, ValueError) as e:
        # Never a reason not to start: the library's own handlers stay in place, so a redeploy
        # still finishes the link in progress. Only the message to the sender is lost.
        logger.warning("Own stop handlers were not installed (%s).", type(e).__name__)


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
    _install_stop_handlers(application)
    if interval_hours() > 0:
        application.bot_data["health_task"] = application.create_task(_health_loop(application))
    else:
        logger.info("Key self-check is disabled (HEALTH_CHECK_INTERVAL_HOURS=0).")


async def _post_shutdown(application: Application) -> None:
    # create_task during post_init is not tracked by PTB, so stop the loop ourselves.
    task = application.bot_data.get("health_task")
    if task is not None:
        task.cancel()


def _build_application(request=None, get_updates_request=None) -> Application:
    builder = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
    )
    # Only the offline lifecycle test passes these, to answer without a network.
    if request is not None:
        builder = builder.request(request)
    if get_updates_request is not None:
        builder = builder.get_updates_request(get_updates_request)
    app = builder.build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app


def _run(application: Application) -> None:
    # The library's own stop handlers are left on: they cover the first moments of start-up,
    # until _post_init replaces them with ours (which tell the sender first). With none at all,
    # a SIGTERM in that window would be ignored - the process is PID 1 in the container.
    application.run_polling(allowed_updates=Update.ALL_TYPES)


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

    app = _build_application()

    logger.info("Bot starting — polling for messages. Press Ctrl+C to stop.")
    _run(app)


if __name__ == "__main__":
    main()
