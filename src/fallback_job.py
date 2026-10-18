"""The no-audio fallback's job: the whole pipeline for one link, run on a GitHub runner
by .github/workflows/process-reel-fallback.yml, with the sender's status messages and
the email exactly as the bot would have sent them (fallback.py explains why).

Refuses to run (exit 2) unless the link is an Instagram link and the chat is on
ALLOWED_CHAT_IDS: anyone holding the dispatch token can start the workflow, and the job
holds every key the pipeline uses.

The repository is public, so the run's log is public. The request is read from the
event file rather than from step variables (GitHub prints those at the top of every
step), its values are masked before anything else is printed, and the pipeline's own
log - which names links, relevance reasons and the recipient - is silenced. The sender
gets the outcome in Telegram; the log records only that the run ended and how.

    REEL_URL=... CHAT_ID=... USER_NOTE=... python src/fallback_job.py   # locally
"""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from authz import parse_allowed_ids  # noqa: E402
from links import INSTAGRAM_URL_PATTERN  # noqa: E402
from messages import t  # noqa: E402
from pipeline import run_pipeline  # noqa: E402
from profile_loader import load_language  # noqa: E402

logger = logging.getLogger("fallback-job")


def read_request(environ) -> tuple[str, str, str]:
    """(url, chat_id, note): from the dispatch event on GitHub, from variables locally."""
    event_path = environ.get("GITHUB_EVENT_PATH")
    if event_path:
        inputs = json.loads(Path(event_path).read_text(encoding="utf-8")).get("inputs") or {}
        return (str(inputs.get("url") or "").strip(), str(inputs.get("chat_id") or "").strip(),
                str(inputs.get("note") or ""))
    return (environ.get("REEL_URL", "").strip(), environ.get("CHAT_ID", "").strip(),
            environ.get("USER_NOTE", ""))


def mask_lines(*values: str) -> list[str]:
    """GitHub workflow commands that hide each value (each line of it) in the log."""
    lines = []
    for value in values:
        for part in (value or "").splitlines():
            if part.strip():
                lines.append(f"::add-mask::{part.strip()}")
    return lines


def allowed_chat(url: str, chat_id: str, allowed_raw: str | None) -> int | None:
    """The chat to report to when this request may run, else None. The whole input
    must be one Instagram link, in the same shape the bot accepts."""
    if not url or not INSTAGRAM_URL_PATTERN.fullmatch(url):
        return None
    try:
        chat = int(chat_id)
    except (TypeError, ValueError):
        return None
    return chat if chat in parse_allowed_ids(allowed_raw) else None


async def run(url: str, note: str | None, language: str, send) -> int:
    """Run the pipeline with `send` as the status channel. 0 when the email went out."""
    result = await run_pipeline(url, status_callback=send, language=language, user_note=note or None)
    if result.get("success"):
        await send(t("email_arrived", language))
        return 0
    if not result.get("user_notified"):
        await send(t("failed_generic", language))
    return 1


async def _run_with_telegram(token: str, chat: int, url: str, note: str, language: str) -> int:
    from telegram import Bot

    async with Bot(token) as bot:
        async def send(msg: str) -> None:
            try:
                await bot.send_message(chat_id=chat, text=msg)
            except Exception as e:
                logger.warning(f"Telegram message failed: {type(e).__name__}")

        return await run(url, note, language, send)


def _quiet_logging() -> None:
    logging.getLogger().setLevel(logging.CRITICAL)  # the pipeline's log stays out of the public run
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def main(environ=os.environ) -> int:
    url, chat_id, note = read_request(environ)
    if environ.get("GITHUB_ACTIONS") == "true":
        for line in mask_lines(url, chat_id, note):
            print(line, flush=True)
    _quiet_logging()

    chat = allowed_chat(url, chat_id, environ.get("ALLOWED_CHAT_IDS"))
    if chat is None:
        logger.error("Refused: not an Instagram link, or the chat is not on ALLOWED_CHAT_IDS.")
        return 2
    token = environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        logger.error("Refused: TELEGRAM_BOT_TOKEN is not set.")
        return 2
    language = load_language(default="en")
    try:
        code = asyncio.run(_run_with_telegram(token, chat, url, note, language))
    except Exception as e:
        logger.error(f"Run crashed: {type(e).__name__}")
        return 1
    logger.info("Finished: the email went out." if code == 0 else "Finished: no email (the sender was told why).")
    return code


if __name__ == "__main__":
    sys.exit(main())
