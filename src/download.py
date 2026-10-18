"""
Download a video from Instagram (or any yt-dlp-supported URL).
Returns the path to the downloaded audio file.

Cookies handling:
  - Locally, yt-dlp can pull cookies straight from your browser (no setup).
  - On a server (Railway / Render / etc.) there's no browser session, so
    Instagram blocks anonymous requests. We support two ways to provide cookies:
    1. Env var INSTAGRAM_COOKIES — paste the contents of a cookies.txt file
       (Netscape format). We'll write it to a temp file at startup.
    2. A cookies.txt file at the project root (git-ignored).
"""
import logging
import os
import re
from pathlib import Path

import yt_dlp
from yt_dlp.utils import DownloadError
from yt_dlp.version import __version__ as YT_DLP_VERSION

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

COOKIES_FILE = PROJECT_ROOT / "cookies.txt"

_VIDEO_ID = re.compile(r"[\w-]+")
_LINK_ID = re.compile(r"/(?:reel|reels|p|tv)/([\w-]+)")


class NoAudioTrackError(Exception):
    """The reel downloaded, but the file has no sound track to transcribe.

    Seen in production (Oct 2026): a few reels reached the server as video only,
    every time, while the same links offer an audio stream elsewhere. Sending the
    link again returns the same file, so the sender is told that instead of "later".
    """


def _is_no_audio_error(exc: DownloadError) -> bool:
    # yt-dlp's audio extraction raises this when ffprobe finds no audio stream.
    return "audio codec" in str(exc)


def _id_from_link(url: str) -> str | None:
    match = _LINK_ID.search(url)
    return match.group(1) if match else None


def _log_format_diagnostics(url: str, ydl_opts: dict) -> str | None:
    """Ask yt-dlp, without downloading, what it picks and what Instagram offers, and
    log it. This is the evidence for why the file had no sound; the signed media
    links in the answer stay out of the log. Returns the video id when it can be read."""
    opts = {k: ydl_opts[k] for k in ("format", "noplaylist", "cookiefile") if k in ydl_opts}
    opts.update(quiet=True, no_warnings=True, simulate=True, socket_timeout=30)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
    except Exception as exc:
        logger.warning(f"Format diagnostics unavailable ({type(exc).__name__}) for {url}")
        return None
    offered = ", ".join(
        f"{f.get('format_id')}:{f.get('ext')}:v={f.get('vcodec')}:a={f.get('acodec')}:{f.get('tbr')}"
        for f in info.get("formats") or []
    )
    logger.warning(
        f"No audio track in the download of {url}. yt-dlp {YT_DLP_VERSION} selected "
        f"format_id={info.get('format_id')} (acodec={info.get('acodec')}, "
        f"vcodec={info.get('vcodec')}). Formats offered: {offered or 'none'}"
    )
    return info.get("id")


def _remove_leftovers(video_id: str | None) -> None:
    """yt-dlp keeps the downloaded video when audio extraction fails, 8-11 MB each time."""
    if not video_id or not _VIDEO_ID.fullmatch(video_id):
        return
    for leftover in DATA_DIR.glob(f"{video_id}.*"):
        try:
            leftover.unlink()
        except OSError as exc:
            logger.warning(f"Cleanup failed for {leftover.name}: {exc}")


def _resolve_cookies_path() -> Path | None:
    """Locate a cookies.txt file. Writes one from env var if present."""
    env_cookies = os.getenv("INSTAGRAM_COOKIES", "").strip()
    if env_cookies:
        COOKIES_FILE.write_text(env_cookies, encoding="utf-8")
        logger.info("Wrote cookies from INSTAGRAM_COOKIES env var.")
        return COOKIES_FILE
    if COOKIES_FILE.exists():
        return COOKIES_FILE
    return None


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

    cookies_path = _resolve_cookies_path()
    if cookies_path is not None:
        ydl_opts["cookiefile"] = str(cookies_path)
        logger.info(f"Using cookies from: {cookies_path.name}")
    else:
        logger.warning(
            "No Instagram cookies available. Anonymous requests are often "
            "blocked by Instagram. If download fails, set the INSTAGRAM_COOKIES "
            "env var with the contents of a cookies.txt export."
        )

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            video_id = info["id"]
    except DownloadError as exc:
        if not _is_no_audio_error(exc):
            raise
        failed_id = _log_format_diagnostics(url, ydl_opts) or _id_from_link(url)
        _remove_leftovers(failed_id)
        raise NoAudioTrackError(f"no audio track in the download of {failed_id}") from exc

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
