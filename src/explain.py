"""
Generate a full, edited explanation of the video's content using Claude Sonnet.

The user's profile (config/profile.md) drives language, tone, and style.
"""
import logging
import os

from anthropic import Anthropic

from profile_loader import load_language, load_profile

logger = logging.getLogger(__name__)

_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


LANGUAGE_NAMES = {
    "he": "Hebrew (עברית)",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "ar": "Arabic",
}


def _build_system_prompt(profile: str, language: str) -> str:
    lang_name = LANGUAGE_NAMES.get(language, language)
    return f"""You are a content assistant who writes full, edited explanations of video content.

You will receive a raw transcript of an Instagram video. Write a complete explanation of the topic.

⚠️ LANGUAGE — STRICT REQUIREMENT:
Write the ENTIRE response in {lang_name}. Do not switch languages mid-response.
If the transcript is in another language, translate the meaning into {lang_name} —
do not mirror the transcript's language.

Other rules:
- A **full** explanation — not a summary. Capture all the information from the video.
- Clean, readable prose. Not raw transcript ("so... um... you know...").
- If concepts are introduced, explain them.
- If steps are shown, list them in order.
- If the speaker gives examples, keep them.
- No openers like "Here is the explanation" or "In this video" — get straight to the content.
- If the transcript is empty, cut off, or unclear — write a single sentence describing the state in {lang_name}.

Style is dictated by the user's profile below.

--- BEGIN USER PROFILE ---
{profile}
--- END USER PROFILE ---
"""


def explain_content(transcript: str, language: str | None = None) -> str:
    """Generate a full content explanation from a raw transcript."""
    if not transcript or len(transcript.strip()) < 20:
        return "(Transcript was empty or too short for a meaningful explanation.)"

    logger.info("Generating content explanation...")
    profile = load_profile()
    if language is None:
        language = load_language()
    client = _get_client()

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        system=_build_system_prompt(profile, language),
        messages=[
            {
                "role": "user",
                "content": (
                    "Below is the transcript of an Instagram video. "
                    "Write a full explanation of the topic, following the user profile.\n\n"
                    f"---\n{transcript}\n---"
                ),
            }
        ],
    )

    text = message.content[0].text.strip()
    logger.info(f"Explanation: {len(text)} chars")

    if message.stop_reason == "max_tokens":
        logger.error(
            f"⚠️ EXPLAIN HIT max_tokens — output likely TRUNCATED. "
            f"Output tokens: {message.usage.output_tokens}. Raise max_tokens further."
        )

    return text


if __name__ == "__main__":
    import sys
    from pathlib import Path
    from dotenv import load_dotenv
    logging.basicConfig(level=logging.INFO)
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    transcript = sys.stdin.read() if not sys.stdin.isatty() else "Test."
    print(explain_content(transcript))
