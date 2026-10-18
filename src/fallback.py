"""Hand a reel to the GitHub Actions fallback when this server gets it without sound.

Measured 18 Oct 2026: for reels that use a song from Instagram's music library,
Instagram answers requests from Railway's network (every Railway region tested) with
the audio muted - "This music can't be played due to recent licensing changes with
the rights holder." The same reels come with sound to GitHub's runners, AWS and a home
connection. So when the download has no audio track, the bot asks a workflow
(.github/workflows/process-reel-fallback.yml) to run the whole pipeline on a GitHub
runner; the workflow sends the sender its own status messages and the email.

Off unless GITHUB_FALLBACK_TOKEN is set: a fine-grained token for this repository
with "Actions: read and write", nothing else.
"""
import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

WORKFLOW_FILE = "process-reel-fallback.yml"
DEFAULT_REPO = "shalhevetvardi/reels-to-email"


def dispatch_fallback(url: str, chat_id, note: str | None) -> bool:
    """Ask GitHub to run the fallback workflow for this link. True only when GitHub
    accepted the request; any failure is logged without the token and returns False,
    so the caller can fall back to telling the sender there is no audio."""
    token = os.getenv("GITHUB_FALLBACK_TOKEN", "").strip()
    if not token:
        logger.info("GITHUB_FALLBACK_TOKEN is not set - the GitHub fallback is off.")
        return False
    repo = os.getenv("GITHUB_FALLBACK_REPO", "").strip() or DEFAULT_REPO
    body = json.dumps({
        "ref": "main",
        "inputs": {"url": url, "chat_id": str(chat_id), "note": note or ""},
    }).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/workflows/{WORKFLOW_FILE}/dispatches",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "reels-to-email-bot",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            accepted = 200 <= response.status < 300
    except urllib.error.HTTPError as e:
        logger.warning(f"GitHub refused the fallback request: HTTP {e.code}")
        return False
    except Exception as e:
        logger.warning(f"GitHub fallback request failed: {type(e).__name__}")
        return False
    if accepted:
        logger.info(f"Handed {url} to the GitHub fallback workflow.")
    return accepted
