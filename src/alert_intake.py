"""Reports how the bot is doing to Yitro's alert intake.

Yitro is the agent that fixes Shalhevet's systems. Its intake (a Cloudflare
Worker, services/yitro-intake in the Storyteling repo) groups reports by
(sender, check): a "fail" opens an incident and wakes Yitro, a later "ok" closes
it. So both outcomes are sent, not only failures.

A client, not a gate: without ALERT_INTAKE_URL / ALERT_INTAKE_TOKEN nothing is
sent, and nothing here ever raises. The alert email and the Telegram message
stay exactly as they were - this is an extra channel.

No personal data and no secret material leaves: summaries are fixed sentences,
plus an exception type or key *names* that the callers already sanitize.
"""
from __future__ import annotations

import logging
import os
import re

import httpx

logger = logging.getLogger(__name__)

SYSTEM = "reels-to-email"
CHECK_PROCESSING = "reels.processing"
CHECK_KEYS = "reels.keys"

_PATH = "/v1/alert"
_TIMEOUT_SECONDS = 10.0
# The intake's own patterns (services/yitro-intake/src/logic.js). A name outside
# them is refused with 400, and the report would be lost without a sound.
CHECK_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
SYSTEM_RE = re.compile(r"^[A-Za-z0-9_.-]{1,60}$")
_SUMMARY_MAX = 500


def notify(check: str, status: str, summary: str) -> bool:
    """Send one report. Returns True when the intake accepted it. Never raises."""
    try:
        url = os.getenv("ALERT_INTAKE_URL", "").strip()
        token = os.getenv("ALERT_INTAKE_TOKEN", "").strip()
        if not url or not token:
            return False
        if status not in ("ok", "fail") or not CHECK_RE.match(check):
            logger.warning("Alert intake: refused to send an invalid report")
            return False
        response = httpx.post(
            url.rstrip("/") + _PATH,
            json={"check": check, "status": status, "system": SYSTEM, "summary": summary[:_SUMMARY_MAX]},
            headers={"Authorization": f"Bearer {token}", "User-Agent": "reels-to-email-intake/1"},
            timeout=_TIMEOUT_SECONDS,
        )
        if 200 <= response.status_code < 300:
            return True
        logger.warning("Alert intake answered %s", response.status_code)
        return False
    except Exception as e:  # noqa: BLE001 - a report must never break the bot
        logger.warning("Alert intake: report not sent (%s)", type(e).__name__)
        return False
