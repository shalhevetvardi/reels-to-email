"""
Transcribe audio/video using OpenAI Whisper.
"""
import logging
import os
from pathlib import Path

from openai import OpenAI

logger = logging.getLogger(__name__)

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


def transcribe_video(video_path: Path) -> str:
    """Transcribe a video/audio file. Returns plain text."""
    logger.info(f"Transcribing: {video_path.name}")
    client = _get_client()

    with open(video_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="text",
        )

    text = result if isinstance(result, str) else result.text
    text = text.strip()
    logger.info(f"Transcription: {len(text)} chars")
    return text


if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    logging.basicConfig(level=logging.INFO)
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    if len(sys.argv) < 2:
        print("Usage: python transcribe.py <video_path>")
        sys.exit(1)
    print(transcribe_video(Path(sys.argv[1])))
