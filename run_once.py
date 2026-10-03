"""
Run the full pipeline once on a specific Instagram URL.
Useful for testing changes without going through the Telegram bot.

Usage:
    python run_once.py "https://www.instagram.com/reel/.../"
    python run_once.py "https://www.instagram.com/p/.../" --no-send --note "focus on the hook"

--no-send writes the email (slides embedded) to data/preview-*.html instead
of sending it, so a run can be inspected without an email going out.
"""
import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

# Make src/ importable
SRC = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(SRC))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)

import resend  # noqa: E402

from pipeline import run_pipeline  # noqa: E402
from profile_loader import load_language  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data"


def _install_preview_sender() -> None:
    """Replace resend.Emails.send with a function that saves the email to data/."""

    def preview_send(params: dict) -> dict:
        DATA_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        html = params.get("html", "")
        for att in params.get("attachments") or []:
            # Embed the slides so the preview is a single self-contained file.
            html = html.replace(
                f"cid:{att['content_id']}",
                f"data:{att.get('content_type', 'image/jpeg')};base64,{att['content']}",
            )
        path = DATA_DIR / f"preview-{stamp}.html"
        path.write_text(html, encoding="utf-8")
        print(f"\nPreview saved (email NOT sent): {path}")
        return {"id": "no-send-preview"}

    resend.Emails.send = preview_send


async def main(url: str, note: str | None = None) -> None:
    async def status(msg: str) -> None:
        print(f"\n>>> {msg}")

    language = load_language(default="he")
    print(f"Language: {language}")
    print(f"URL: {url}\n")

    result = await run_pipeline(url, status_callback=status, language=language, user_note=note)

    print("\n" + "=" * 60)
    print(f"Result: {result}")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the pipeline once on an Instagram URL.")
    parser.add_argument("url", help="Instagram reel / post URL")
    parser.add_argument("--no-send", action="store_true", help="save the email under data/ instead of sending it")
    parser.add_argument("--note", default=None, help="extra note passed to the analysis (like text next to the link in Telegram)")
    args = parser.parse_args()

    if args.no_send:
        _install_preview_sender()
    asyncio.run(main(args.url, args.note))
