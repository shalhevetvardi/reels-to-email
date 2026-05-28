"""
Orchestrator — runs the full pipeline for a single Instagram URL.

Stages:
  1. Download audio
  2. Transcribe
  3. Generate full explanation (in the user's language, per profile)
  4. Research the topic online
  5. Tag relevance based on the user's profile
  6. Send email with everything
"""
import logging
from typing import Awaitable, Callable, Optional

from download import download_video
from email_sender import send_email
from explain import explain_content
from messages import t
from research import research_topic
from tag import tag_relevance
from transcribe import transcribe_video

logger = logging.getLogger(__name__)

StatusCallback = Optional[Callable[[str], Awaitable[None]]]


async def _notify(callback: StatusCallback, msg: str) -> None:
    logger.info(msg)
    if callback is not None:
        try:
            await callback(msg)
        except Exception as e:
            logger.warning(f"Status callback failed: {e}")


async def run_pipeline(
    instagram_url: str,
    status_callback: StatusCallback = None,
    language: str = "en",
) -> dict:
    """Run the full pipeline. Returns a dict with success/error info."""
    audio_path = None
    try:
        await _notify(status_callback, t("downloading", language))
        audio_path = download_video(instagram_url)

        await _notify(status_callback, t("transcribing", language))
        transcript = transcribe_video(audio_path)

        await _notify(status_callback, t("explaining", language))
        explanation = explain_content(transcript)

        await _notify(status_callback, t("researching", language))
        research = research_topic(explanation)

        await _notify(status_callback, t("tagging", language))
        tag = tag_relevance(explanation)

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
            "email_id": email_id,
            "tag": tag,
            "explanation_length": len(explanation),
        }

    except Exception as e:
        logger.exception("Pipeline failed")
        await _notify(
            status_callback,
            t(
                "failed_step",
                language,
                error_type=type(e).__name__,
                error_message=str(e)[:200],
            ),
        )
        return {"success": False, "error": f"{type(e).__name__}: {e}"}

    finally:
        # Cleanup downloaded audio
        if audio_path is not None and audio_path.exists():
            try:
                audio_path.unlink()
                logger.info(f"Cleaned up: {audio_path.name}")
            except Exception as e:
                logger.warning(f"Cleanup failed: {e}")
