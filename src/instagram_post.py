"""
Probe an Instagram post and fetch its slide media (carousels / image posts).

yt-dlp can describe a post without downloading it: a carousel comes back as a
playlist whose entries are the slides. Image slides have no formats; video
slides do. This module turns that into plain dataclasses and downloads the
slide stills through a strict host allow-list (the URLs come from a response
we do not control, so they must never be allowed to point at internal hosts).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import yt_dlp

from download import DATA_DIR, _resolve_cookies_path

logger = logging.getLogger(__name__)

ALLOWED_MEDIA_HOST_SUFFIXES = (".cdninstagram.com", ".fbcdn.net")
MAX_IMAGE_BYTES = 3_500_000  # comfortably under the vision API per-image limit
# All slides travel in one vision request; base64 adds a third, and the API
# rejects requests over ~32 MB.
MAX_TOTAL_IMAGE_BYTES = 18_000_000
DEFAULT_MAX_SLIDES = 20  # Instagram's own carousel limit
ALLOWED_IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")

_CROP_TOKEN = re.compile(r"(^|_)c\d+(\.\d+){3}a?(_|$)")
_HOSTNAME = re.compile(r"[a-z0-9.-]+")
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass(frozen=True)
class Slide:
    index: int  # 1-based
    kind: str  # "image" | "video"
    image_url: str | None  # best still (cover frame for video slides); None if no allowed URL
    duration: float | None = None
    entry: dict | None = field(default=None, compare=False, repr=False)  # raw yt-dlp entry, video slides only


@dataclass(frozen=True)
class Post:
    shortcode: str
    url: str
    author: str  # username ("channel")
    author_name: str  # full name ("uploader")
    caption: str
    like_count: int | None
    comment_count: int | None
    slides: tuple[Slide, ...]

    @property
    def is_single_video(self) -> bool:
        return len(self.slides) == 1 and self.slides[0].kind == "video"


@dataclass(frozen=True)
class SlideImage:
    index: int
    kind: str
    data: bytes
    media_type: str


def needs_probe(url: str) -> bool:
    """True for post/share links, which may be carousels or image posts.

    Reels keep going straight to the existing video flow, so they cost zero
    extra Instagram requests.
    """
    try:
        segments = [s for s in urlsplit(url).path.lower().split("/") if s]
    except ValueError:
        return False
    return "p" in segments or "share" in segments


def extract_user_note(text: str, max_chars: int = 500) -> str | None:
    """Return what the sender typed besides the Instagram link, or None.

    The whole whitespace-delimited word that holds a link is dropped, so
    leftovers of the URL (tracking parameters the link regex did not match)
    never reach the analysis as if they were a note.
    """
    words = [w for w in text.split() if "instagram.com" not in w.lower() and "instagr.am" not in w.lower()]
    note = " ".join(words)[:max_chars].strip()
    return note or None


def is_allowed_media_url(url: str) -> bool:
    """SSRF guard: only https URLs on Instagram's own media CDN hosts."""
    # URL parsers disagree about whitespace, control characters and backslashes;
    # refuse them outright instead of guessing how the HTTP client reads them.
    if not isinstance(url, str) or any(ord(c) < 33 or ord(c) == 127 or c == "\\" for c in url):
        return False
    try:
        parts = urlsplit(url)
        if parts.scheme != "https":
            return False
        if parts.username is not None or parts.password is not None or "@" in parts.netloc:
            return False
        if parts.port not in (None, 443):
            return False
        host = (parts.hostname or "").lower()
    except (ValueError, AttributeError):
        return False
    return bool(_HOSTNAME.fullmatch(host)) and host.endswith(ALLOWED_MEDIA_HOST_SUFFIXES)


def _is_cropped(url: str) -> bool:
    stp = parse_qs(urlsplit(url).query).get("stp", [""])[0]
    return bool(_CROP_TOKEN.search(stp))


def pick_image_url(thumbnails: list[dict]) -> str | None:
    """Pick the best uncropped still from a yt-dlp thumbnails list (worst -> best)."""
    allowed = [
        t for t in thumbnails
        if isinstance(t, dict) and isinstance(t.get("url"), str) and is_allowed_media_url(t["url"])
    ]
    if not allowed:
        return None
    uncropped = [t for t in allowed if not _is_cropped(t["url"])]
    candidates = uncropped or allowed

    def _area(t: dict) -> int | None:
        w, h = t.get("width"), t.get("height")
        if isinstance(w, int) and isinstance(h, int):
            return w * h
        return None

    if any(_area(t) is not None for t in candidates):
        best = None
        best_area = -1
        for t in candidates:  # ties go to the later one
            area = _area(t)
            if area is not None and area >= best_area:
                best, best_area = t, area
        return best["url"]
    return candidates[-1]["url"]


def _as_int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _as_float(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def parse_post(info: dict, url: str) -> Post:
    """Turn a yt-dlp info dict (playlist or single item) into a Post."""
    entries = list(info["entries"]) if "entries" in info else [info]
    if not entries:
        raise ValueError("post has no entries")

    slides = []
    for i, entry in enumerate(entries, start=1):
        is_video = bool(entry.get("formats"))
        slides.append(
            Slide(
                index=i,
                kind="video" if is_video else "image",
                image_url=pick_image_url(entry.get("thumbnails") or []),
                duration=_as_float(entry.get("duration")),
                entry=entry if is_video else None,
            )
        )

    return Post(
        shortcode=info.get("id") or "",
        url=url,
        author=info.get("channel") or "",
        author_name=info.get("uploader") or "",
        caption=info.get("description") or "",
        like_count=_as_int(info.get("like_count")),
        comment_count=_as_int(info.get("comment_count")),
        slides=tuple(slides),
    )


def probe_post(url: str) -> Post:
    """Ask yt-dlp what the post contains, without downloading any media."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignore_no_formats_error": True,
    }
    cookies_path = _resolve_cookies_path()
    if cookies_path is not None:
        opts["cookiefile"] = str(cookies_path)

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False, process=False)
    return parse_post(info, url)


def download_image(
    url: str, *, max_bytes: int = MAX_IMAGE_BYTES, timeout: float = 30.0
) -> tuple[bytes, str]:
    """Download one slide image. Returns (data, media_type)."""
    if not is_allowed_media_url(url):
        raise ValueError("image URL is not on an allowed media host")

    headers = {"Referer": "https://www.instagram.com/", "User-Agent": _USER_AGENT}
    with httpx.Client(follow_redirects=False, timeout=timeout) as client:
        with client.stream("GET", url, headers=headers) as response:
            if response.status_code != 200:
                raise ValueError(f"unexpected status {response.status_code}")
            media_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if media_type not in ALLOWED_IMAGE_TYPES:
                raise ValueError(f"unsupported content type {media_type or '(none)'}")
            chunks = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"image larger than {max_bytes} bytes")
                chunks.append(chunk)
    return b"".join(chunks), media_type


def download_slide_images(
    post: Post,
    max_slides: int = DEFAULT_MAX_SLIDES,
    max_total_bytes: int = MAX_TOTAL_IMAGE_BYTES,
) -> list[SlideImage]:
    """Download the still of every slide that has one. Failed slides are skipped.

    Stops once the images together would exceed `max_total_bytes`.
    """
    images = []
    total = 0
    for slide in post.slides[:max_slides]:
        if not slide.image_url:
            continue
        try:
            data, media_type = download_image(slide.image_url)
        except Exception as e:
            logger.warning("Slide %d image download failed: %s", slide.index, type(e).__name__)
            continue
        if total + len(data) > max_total_bytes:
            logger.warning("Slide images exceed the total size budget - stopping at slide %d", slide.index)
            break
        total += len(data)
        images.append(SlideImage(index=slide.index, kind=slide.kind, data=data, media_type=media_type))
    return images


def download_slide_audio(post: Post, slide: Slide) -> Path | None:
    """Extract the audio of a video slide as mp3, or None if that is not possible."""
    if slide.kind != "video" or not slide.entry:
        return None
    try:
        opts = {
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
            opts["cookiefile"] = str(cookies_path)

        entry = {
            **slide.entry,
            "extractor": "Instagram",
            "extractor_key": "Instagram",
            "webpage_url": post.url,
            "original_url": post.url,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.process_ie_result(entry, download=True)

        path = DATA_DIR / f"{slide.entry.get('id')}.mp3"
        return path if path.exists() else None
    except Exception as e:
        logger.warning("Slide %d audio extraction failed: %s", slide.index, type(e).__name__)
        return None
