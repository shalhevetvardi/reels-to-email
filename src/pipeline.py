"""
Orchestrator — runs the full pipeline for a single Instagram URL.

Stages:
  1. Download audio
  2. Transcribe
  3. Generate full explanation (in the user's language, per profile)
  4. Research the topic online
  5. Tag relevance based on the user's profile
  6. Send email with everything

Posts with several slides (carousels) or a single image take a separate path:
download the slide images, analyse them with Claude vision, then research,
tag and email like the video flow does. Reels never touch that path.
"""
import logging
import os
from typing import Awaitable, Callable, Optional

from carousel_analyze import SlideInput, analyze_carousel
from download import InstagramBlockedError, NoAudioTrackError, download_video
from email_sender import send_carousel_email, send_email
from explain import explain_content
from instagram_post import (
    DEFAULT_MAX_SLIDES,
    Post,
    download_slide_audio,
    download_slide_images,
    needs_probe,
    probe_post,
)
from messages import t
from research import research_topic
from tag import tag_relevance
from transcribe import transcribe_video

logger = logging.getLogger(__name__)

StatusCallback = Optional[Callable[[str], Awaitable[None]]]
# Called when this server cannot get the reel as it should: no audio track, or Instagram's
# login wall. True means another runner took the link over (fallback.py) and will tell the
# sender itself.
HandOffHandler = Optional[Callable[[], Awaitable[bool]]]

# Whisper costs money per minute; a few video slides are enough to get the gist.
MAX_VIDEO_SLIDE_TRANSCRIPTS = 5

_IMAGE_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}

# The research and tagging prompts speak about "a video"; tell them what this really is.
_SOURCE_NOTE = (
    "(Note: the source is an Instagram carousel - a multi-image post, not a video. "
    "Refer to it as a carousel.)\n\n"
)


def _max_slides() -> int:
    """CAROUSEL_MAX_SLIDES, clamped to 1..DEFAULT_MAX_SLIDES; invalid -> default."""
    try:
        value = int(os.getenv("CAROUSEL_MAX_SLIDES", ""))
    except ValueError:
        return DEFAULT_MAX_SLIDES
    return min(max(value, 1), DEFAULT_MAX_SLIDES)


async def _notify(callback: StatusCallback, msg: str) -> bool:
    """Send one status message. True only when it was delivered, so a failure result
    can say whether the sender already knows (the bot then skips its own reply)."""
    logger.info(msg)
    if callback is None:
        return False
    try:
        await callback(msg)
    except Exception as e:
        logger.warning(f"Status callback failed: {e}")
        return False
    return True


async def _try_hand_off(hand_off: HandOffHandler) -> bool:
    """Ask the hook to take the link over. Any failure of the hook counts as no."""
    if hand_off is None:
        return False
    try:
        return await hand_off() is True
    except Exception as exc:
        logger.warning(f"Hand-off failed: {type(exc).__name__}")
        return False


async def _run_video_pipeline(
    instagram_url: str,
    status_callback: StatusCallback = None,
    language: str = "en",
    hand_off: HandOffHandler = None,
) -> dict:
    """Run the full pipeline. Returns a dict with success/error info."""
    audio_path = None
    try:
        await _notify(status_callback, t("downloading", language))
        audio_path = download_video(instagram_url)

        await _notify(status_callback, t("transcribing", language))
        transcript = transcribe_video(audio_path)

        await _notify(status_callback, t("explaining", language))
        explanation = explain_content(transcript, language=language)

        await _notify(status_callback, t("researching", language))
        research = research_topic(explanation, language=language)

        await _notify(status_callback, t("tagging", language))
        tag = tag_relevance(explanation, language=language)

        await _notify(status_callback, t("sending_email", language))
        email_id = send_email(
            instagram_url=instagram_url,
            explanation=explanation,
            research=research,
            relevant=tag["relevant"],
            reason=tag["reason"],
        )

        relevance_label = t("relevant" if tag["relevant"] else "not_relevant", language)
        await _notify(
            status_callback,
            t("done", language, relevance=relevance_label),
        )

        return {
            "success": True,
            "kind": "video",
            "email_id": email_id,
            "tag": tag,
            "explanation_length": len(explanation),
        }

    except NoAudioTrackError as e:
        # A known per-reel limit, not a fault: download.py already logged why.
        logger.warning(f"Reel has no audio track: {e}")
        handed_off = await _try_hand_off(hand_off)
        message = t("no_audio_fallback" if handed_off else "no_audio", language)
        told = await _notify(status_callback, message)
        return {
            "success": False,
            "error": f"{type(e).__name__}: {e}",
            "user_notified": told,
            "handed_off": handed_off,
        }

    except InstagramBlockedError as e:
        logger.warning(f"Instagram refused the download: {e}")
        if await _try_hand_off(hand_off):
            told = await _notify(status_callback, t("blocked_fallback", language))
            return {
                "success": False,
                "error": f"{type(e).__name__}: {e}",
                "user_notified": told,
                "handed_off": True,
            }
        # Nobody took it over: the same answer and failure report as before this existed.
        logger.exception("Pipeline failed")
        told = await _notify(status_callback, t("failed_generic", language))
        return {"success": False, "error": f"{type(e).__name__}: {e}", "user_notified": told}

    except Exception as e:
        logger.exception("Pipeline failed")
        # Generic message to the user; full detail is already in logger.exception
        # above (do not echo exception text back to the sender).
        told = await _notify(status_callback, t("failed_generic", language))
        return {"success": False, "error": f"{type(e).__name__}: {e}", "user_notified": told}

    finally:
        # Cleanup downloaded audio
        if audio_path is not None and audio_path.exists():
            try:
                audio_path.unlink()
                logger.info(f"Cleaned up: {audio_path.name}")
            except Exception as e:
                logger.warning(f"Cleanup failed: {e}")


async def _run_carousel_pipeline(
    post: Post,
    status_callback: StatusCallback,
    language: str,
    user_note: Optional[str],
) -> dict:
    """Carousel / image-post flow. Returns a dict with success/error info."""
    try:
        if len(post.slides) == 1:
            await _notify(status_callback, t("post_detected", language))
        else:
            await _notify(status_callback, t("carousel_detected", language, count=len(post.slides)))

        max_slides = _max_slides()
        images = download_slide_images(post, max_slides)
        if not images:
            raise RuntimeError("no slide images could be downloaded")

        used_slides = post.slides[:max_slides]
        transcripts: dict[int, str] = {}
        video_slides = [s for s in used_slides if s.kind == "video"]
        if video_slides:
            await _notify(status_callback, t("carousel_transcribing", language))
        for slide in video_slides[:MAX_VIDEO_SLIDE_TRANSCRIPTS]:
            audio = download_slide_audio(post, slide)
            if audio is None:
                continue
            try:
                transcripts[slide.index] = transcribe_video(audio)
            except Exception as e:
                logger.warning(f"Slide {slide.index} transcription failed: {type(e).__name__}")
            finally:
                try:
                    audio.unlink()
                except Exception as e:
                    logger.warning(f"Cleanup failed: {e}")

        by_index = {img.index: img for img in images}
        slide_inputs = []
        for slide in used_slides:
            img = by_index.get(slide.index)
            slide_inputs.append(
                SlideInput(
                    index=slide.index,
                    kind=slide.kind,
                    image_data=img.data if img else None,
                    media_type=img.media_type if img else None,
                    transcript=transcripts.get(slide.index),
                )
            )

        await _notify(status_callback, t("carousel_analyzing", language))
        analysis = analyze_carousel(
            slide_inputs,
            caption=post.caption,
            author=post.author,
            url=post.url,
            like_count=post.like_count,
            comment_count=post.comment_count,
            user_note=user_note,
            language=language,
        )

        await _notify(status_callback, t("researching", language))
        try:
            research = research_topic(_SOURCE_NOTE + analysis.content, language=language)
        except Exception as e:
            # Research is secondary for carousels; the email must still go out.
            logger.warning(f"Research failed, sending without it: {type(e).__name__}")
            research = ""

        await _notify(status_callback, t("tagging", language))
        tag = tag_relevance(_SOURCE_NOTE + analysis.content, language=language)

        await _notify(status_callback, t("sending_email", language))
        email_id = send_carousel_email(
            instagram_url=post.url,
            author=post.author,
            slide_count=len(post.slides),
            content=analysis.content,
            structure=analysis.structure,
            brief=analysis.brief,
            research=research,
            relevant=tag["relevant"],
            reason=tag["reason"],
            images=[
                (
                    f"slide-{img.index:02d}.{_IMAGE_EXTENSIONS.get(img.media_type, 'jpg')}",
                    img.data,
                    img.media_type,
                )
                for img in images
            ],
        )

        relevance_label = t("relevant" if tag["relevant"] else "not_relevant", language)
        await _notify(
            status_callback,
            t("done", language, relevance=relevance_label),
        )

        return {
            "success": True,
            "kind": "carousel",
            "email_id": email_id,
            "tag": tag,
            "slides": len(post.slides),
        }

    except Exception as e:
        logger.exception("Carousel pipeline failed")
        told = await _notify(status_callback, t("failed_generic", language))
        return {"success": False, "error": f"{type(e).__name__}: {e}", "user_notified": told}


async def run_pipeline(
    instagram_url: str,
    status_callback: StatusCallback = None,
    language: str = "en",
    user_note: Optional[str] = None,
    hand_off: HandOffHandler = None,
) -> dict:
    """Run the pipeline for one URL, picking the carousel or the video flow."""
    # Only post/share links can be carousels; reels skip the probe entirely.
    if needs_probe(instagram_url):
        post = None
        try:
            await _notify(status_callback, t("probing", language))
            post = probe_post(instagram_url)
        except Exception:
            logger.exception("Post probe failed - falling back to the video pipeline")
        if post is not None and not post.is_single_video:
            return await _run_carousel_pipeline(post, status_callback, language, user_note)
    return await _run_video_pipeline(instagram_url, status_callback, language, hand_off)
