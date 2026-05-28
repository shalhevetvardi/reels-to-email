"""
Profile loader — reads the user's personal configuration.

Load order:
  1. Environment variable PROFILE_CONTENT (used on cloud platforms like Railway)
  2. Local file config/profile.md (used during local development)
  3. Error with clear instructions

This dual-source approach lets the same code run identically on a developer's
laptop (file-based) and on a deployed server (env-var-based), with no code change.
"""
import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = PROJECT_ROOT / "config" / "profile.md"
PROFILE_EXAMPLE_PATH = PROJECT_ROOT / "config" / "profile.md.example"


def load_profile() -> str:
    """Return the user's profile text. Tries env var first, then local file."""
    # 1. Environment variable (used on cloud deployments)
    env_content = os.getenv("PROFILE_CONTENT", "").strip()
    if env_content:
        return env_content

    # 2. Local file (used during local development)
    if PROFILE_PATH.exists():
        text = PROFILE_PATH.read_text(encoding="utf-8").strip()
        if text:
            return text

    # 3. Neither available — fail clearly
    raise RuntimeError(
        "\n\n"
        "❌ No profile found.\n\n"
        "You need to set up your personal profile so the AI knows your\n"
        "language, topics, and style.\n\n"
        "Choose one of:\n\n"
        "  LOCAL DEVELOPMENT:\n"
        "    1. cp config/profile.md.example config/profile.md\n"
        "    2. Edit config/profile.md to describe yourself.\n\n"
        "  CLOUD DEPLOYMENT (Railway / Render / Fly / etc.):\n"
        "    1. Open config/profile.md.example as a starting point.\n"
        "    2. In your platform's dashboard, add an environment variable:\n"
        "         Name:  PROFILE_CONTENT\n"
        "         Value: (the full contents of your filled-in profile.md)\n\n"
    )


_LANGUAGE_PATTERN = re.compile(
    r"##\s*Language\s*\n+\s*(?:```[a-z]*\s*\n*)?([a-z]{2,5})",
    re.IGNORECASE,
)


def load_language(default: str = "en") -> str:
    """Return the user's language code.

    Resolution order:
      1. INTERFACE_LANGUAGE env var (highest priority — easy cloud override)
      2. `## Language` section inside the profile
      3. `default` argument
    """
    env_lang = os.getenv("INTERFACE_LANGUAGE", "").strip().lower()
    if env_lang:
        return env_lang

    try:
        profile = load_profile()
    except RuntimeError:
        return default

    match = _LANGUAGE_PATTERN.search(profile)
    if match:
        return match.group(1).strip().lower()
    return default


if __name__ == "__main__":
    print("Language:", load_language())
    print("---")
    print(load_profile())
