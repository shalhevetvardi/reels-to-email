"""
Profile loader — reads the user's personal configuration from config/profile.md.

Why this file exists:
- The pipeline code itself is generic.
- What's personal (language, voice, relevance criteria) lives in config/profile.md.
- config/profile.md is git-ignored — each user maintains their own.
- A template config/profile.md.example is committed for new users.
"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = PROJECT_ROOT / "config" / "profile.md"
PROFILE_EXAMPLE_PATH = PROJECT_ROOT / "config" / "profile.md.example"


def load_profile() -> str:
    """Return the full text of config/profile.md.

    Raises a clear, actionable error if the file isn't set up yet.
    """
    if not PROFILE_PATH.exists():
        raise RuntimeError(
            f"\n\n"
            f"❌ Profile file not found at: {PROFILE_PATH}\n\n"
            f"To set up:\n"
            f"  1. cp config/profile.md.example config/profile.md\n"
            f"  2. Edit config/profile.md — fill in your language, topics, and style.\n"
            f"  3. Re-run the bot.\n"
        )
    text = PROFILE_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"Profile file at {PROFILE_PATH} is empty.")
    return text


if __name__ == "__main__":
    print(load_profile())
