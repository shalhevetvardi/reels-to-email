"""
Download a video from Instagram (or any yt-dlp-supported URL).
Returns the path to the downloaded video file.
"""
import logging
from pathlib import Path

import yt_dlp

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)


def download_video(url: str) -> Path:
    """Download audio only and return the local file path.

    We extract audio-only (not video) because:
    1. Whisper only needs audio, not pixels.
    2. Whisper has a 25 MB upload limit — full Instagram videos easily exceed it.
    Audio is 10-20× smaller than the full video.

    Requires ffmpeg installed on the system.
    """
    logger.info(f"Downloading audio from: {url}")

    ydl_opts = {
        # Grab the best audio stream, then convert to mp3 at low bitrate
        # (speech is fine at 64 kbps and stays well under 25 MB even for long videos)
        "format": "bestaudio/best",
        "outtmpl": str(DATA_DIR / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "64",
            }
        ],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        video_id = info["id"]

    # After post-processing, the file should be {id}.mp3
    path = DATA_DIR / f"{video_id}.mp3"
    if not path.exists():
        # Fall back: look for any file with this id (in case ffmpeg failed)
        candidates = list(DATA_DIR.glob(f"{video_id}.*"))
        if not candidates:
            raise FileNotFoundError(
                f"Audio file not found after download: {video_id}. "
                f"Make sure ffmpeg is installed (`brew install ffmpeg`)."
            )
        path = candidates[0]

    size_mb = path.stat().st_size / (1024 * 1024)
    logger.info(f"Downloaded audio: {size_mb:.2f} MB -> {path.name}")

    if size_mb > 24:
        logger.warning(
            f"Audio file is {size_mb:.1f} MB — close to Whisper's 25 MB limit. "
            f"Very long videos may still fail."
        )

    return path


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 2:
        print("Usage: python download.py <instagram_url>")
        sys.exit(1)
    print(download_video(sys.argv[1]))
