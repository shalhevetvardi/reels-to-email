"""
Run the full pipeline once on a specific Instagram URL.
Useful for testing changes without going through the Telegram bot.

Usage:
    python run_once.py "https://www.instagram.com/reel/.../"
"""
import asyncio
import logging
import sys
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

from pipeline import run_pipeline  # noqa: E402
from profile_loader import load_language  # noqa: E402


async def main(url: str) -> None:
    async def status(msg: str) -> None:
        print(f"\n>>> {msg}")

    language = load_language(default="he")
    print(f"Language: {language}")
    print(f"URL: {url}\n")

    result = await run_pipeline(url, status_callback=status, language=language)

    print("\n" + "=" * 60)
    print(f"Result: {result}")
    print("=" * 60)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python run_once.py <instagram_url>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
