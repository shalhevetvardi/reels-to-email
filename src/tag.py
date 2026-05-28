"""
Tag content as relevant-for-the-user or not, based on config/profile.md.
"""
import json
import logging
import os
import re

from anthropic import Anthropic

from profile_loader import load_language, load_profile

_LANGUAGE_NAMES = {
    "he": "Hebrew (עברית)",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "ar": "Arabic",
}

logger = logging.getLogger(__name__)

_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


def _parse_json_loose(raw: str) -> dict:
    """Parse JSON that may be wrapped in markdown code fences."""
    raw = raw.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fence_match:
        raw = fence_match.group(1)
    brace_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if brace_match:
        raw = brace_match.group(0)
    return json.loads(raw)


def tag_relevance(explanation: str, language: str | None = None) -> dict:
    """Tag content. Returns {'relevant': bool, 'reason': str}."""
    profile = load_profile()
    if language is None:
        language = load_language()
    lang_name = _LANGUAGE_NAMES.get(language, language)

    system_prompt = (
        "You are a content classifier. You will receive an explanation of "
        "an Instagram video's content. Your task: decide if the topic is "
        "relevant to the user based on their profile below.\n\n"
        "--- BEGIN USER PROFILE ---\n"
        f"{profile}\n"
        "--- END USER PROFILE ---\n\n"
        f"⚠️ The 'reason' field MUST be written in {lang_name}. Not English unless that's the user's language.\n\n"
        "Return JSON only, no other text, in this exact shape:\n"
        f'{{"relevant": true|false, "reason": "one sentence in {lang_name} explaining why"}}'
    )

    logger.info("Tagging relevance...")
    client = _get_client()
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": f"Content explanation:\n\n---\n{explanation}\n---\n\nReturn JSON only.",
            }
        ],
    )

    raw = message.content[0].text.strip()
    try:
        result = _parse_json_loose(raw)
        relevant = bool(result.get("relevant", False))
        reason = str(result.get("reason", "")).strip()
        if not reason:
            reason = "(no reason provided)"
    except Exception as e:
        logger.warning(f"Tag JSON parse failed: {e}; raw={raw[:200]}")
        # Default to "relevant" so user can decide (the typical "when in doubt" rule)
        relevant = True
        reason = "Auto-tagging failed — defaulted to relevant."

    logger.info(f"Tag: relevant={relevant}, reason={reason}")
    return {"relevant": relevant, "reason": reason}


if __name__ == "__main__":
    import sys
    from pathlib import Path
    from dotenv import load_dotenv
    logging.basicConfig(level=logging.INFO)
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    text = sys.stdin.read() if not sys.stdin.isatty() else "Tutorial on building AI agents."
    print(tag_relevance(text))
