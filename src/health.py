"""
Daily self-check of the API keys the bot depends on, plus failure alerts.

One key was revoked and the bot silently failed for a month, because the
Telegram failure message is generic by design and nobody read the server log.
This module checks each key with a free, read-only provider call and, when a
key is definitely refused, alerts the owner on two channels (Telegram + email).

No Telegram imports here: the senders are passed in, so everything is unit
testable. Secrets rule: a key value or a raw response body is never logged,
returned or put in an alert.
"""
import asyncio
import logging
import math
import os
import re
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Sequence

import httpx

from messages import t

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 20
_DEFAULT_INTERVAL_HOURS = 24.0
_GAP_DAILY_SECONDS = 20 * 3600
_GAP_FAILURE_SECONDS = 3600
_DETAIL_MAX = 40
_FAILURE_TYPE_MAX = 60

# Some providers sit behind a bot filter that rejects the default httpx agent.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

OK = "ok"
REJECTED = "rejected"
MISSING = "missing"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class KeyStatus:
    name: str  # env var name, e.g. "OPENAI_API_KEY"
    service: str  # "OpenAI" | "Anthropic" | "Perplexity" | "Resend"
    state: str  # "ok" | "rejected" | "missing" | "unknown"
    detail: str  # short and safe: never the key, never a response body


@dataclass(frozen=True)
class _Check:
    name: str
    service: str
    method: str
    url: str
    headers: Callable[[str], dict]
    json_body: dict | None
    ok_statuses: frozenset
    rejected_statuses: frozenset


def _bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


_CHECKS: tuple[_Check, ...] = (
    _Check(
        "OPENAI_API_KEY", "OpenAI", "GET", "https://api.openai.com/v1/models",
        _bearer, None, frozenset({200}), frozenset({401, 403}),
    ),
    _Check(
        "ANTHROPIC_API_KEY", "Anthropic", "GET", "https://api.anthropic.com/v1/models",
        lambda key: {"x-api-key": key, "anthropic-version": "2023-06-01"},
        None, frozenset({200}), frozenset({401, 403}),
    ),
    # An empty body is refused with 400/422 before any model runs, so no spend.
    _Check(
        "PERPLEXITY_API_KEY", "Perplexity", "POST", "https://api.perplexity.ai/chat/completions",
        _bearer, {}, frozenset({400, 422}), frozenset({401, 403}),
    ),
    # Resend is special-cased below: a send-only key gets 401 restricted_api_key.
    _Check(
        "RESEND_API_KEY", "Resend", "GET", "https://api.resend.com/domains",
        _bearer, None, frozenset({200}), frozenset({400, 401, 403}),
    ),
)


def _sanitize(value: object, allowed: str, limit: int) -> str:
    return re.sub(f"[^{allowed}]", "", str(value))[:limit]


def _provider_error_type(response: "httpx.Response") -> str:
    """The provider's error type field, or "" - read from JSON only, never the message."""
    try:
        data = response.json()
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    candidates = []
    error = data.get("error")
    if isinstance(error, dict):
        candidates += [error.get("type"), error.get("code")]
    candidates.append(data.get("name"))
    for value in candidates:
        if isinstance(value, str) and value:
            return _sanitize(value, "A-Za-z0-9_", _DETAIL_MAX)
    return ""


def _echoes_key(error_type: str, key: str) -> bool:
    """True when the provider's error field repeats a piece of the key."""
    plain = _sanitize(key, "A-Za-z0-9_", len(key))
    return any(error_type[i:i + 8] in plain for i in range(max(len(error_type) - 7, 0)))


def _detail(status_code: int, error_type: str) -> str:
    text = f"HTTP {status_code}" + (f" {error_type}" if error_type else "")
    return text[:_DETAIL_MAX]


def _run_check(check: _Check) -> KeyStatus:
    def status(state: str, detail: str) -> KeyStatus:
        return KeyStatus(check.name, check.service, state, detail)

    key = (os.getenv(check.name) or "").strip()
    if not key:
        return status(MISSING, "not set")

    try:
        response = httpx.request(
            check.method,
            check.url,
            headers={**check.headers(key), "User-Agent": _USER_AGENT},
            json=check.json_body,
            timeout=_TIMEOUT_SECONDS,
            follow_redirects=False,
        )
        code = response.status_code
        error_type = _provider_error_type(response)
        if _echoes_key(error_type, key):
            # A provider that echoes the key back must not get it into an alert.
            error_type = ""
    except Exception as e:
        # Network trouble says nothing about the key; only a refusal does.
        return status(UNKNOWN, _sanitize(type(e).__name__, "A-Za-z0-9_", _DETAIL_MAX))

    detail = _detail(code, error_type)
    if check.name == "RESEND_API_KEY" and code == 401 and error_type == "restricted_api_key":
        return status(OK, detail)
    if code in check.ok_statuses:
        return status(OK, detail)
    if code in check.rejected_statuses:
        return status(REJECTED, detail)
    return status(UNKNOWN, detail)


def check_all() -> list[KeyStatus]:
    """Check the four keys, in a fixed order. Never raises."""
    return [_run_check(check) for check in _CHECKS]


def problems(statuses: Sequence[KeyStatus]) -> list[KeyStatus]:
    return [s for s in statuses if s.state in (REJECTED, MISSING)]


def should_alert(
    last_sent: dict[str, float], signature: str, now: float, min_gap_seconds: float
) -> bool:
    if signature not in last_sent:
        return True
    return now - last_sent[signature] >= min_gap_seconds


def interval_hours() -> float:
    """HEALTH_CHECK_INTERVAL_HOURS: default 24, invalid -> 24, <= 0 -> 0 (disabled)."""
    raw = os.getenv("HEALTH_CHECK_INTERVAL_HOURS")
    if raw is None or not raw.strip():
        return _DEFAULT_INTERVAL_HOURS
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_INTERVAL_HOURS
    if not math.isfinite(value):
        return _DEFAULT_INTERVAL_HOURS
    return value if value > 0 else 0.0


SendTelegram = Callable[[str], Awaitable[None]]
SendEmail = Callable[[str, Sequence[str]], object]

# signature -> time of the last alert. Process-local: a restart may repeat one alert, which is fine.
_last_alert: dict[str, float] = {}


async def run_health_check(
    send_telegram: SendTelegram,
    send_email: SendEmail,
    *,
    language: str,
    reason: str = "daily",
    now: float | None = None,
) -> list[KeyStatus]:
    """Check the keys; alert on Telegram and email when one is definitely refused."""
    statuses = await asyncio.to_thread(check_all)
    bad = problems(statuses)
    if not bad:
        return statuses

    now = time.time() if now is None else now
    signature = ",".join(sorted(s.name for s in bad))
    gap = _GAP_FAILURE_SECONDS if reason == "failure" else _GAP_DAILY_SECONDS
    if not should_alert(_last_alert, signature, now, gap):
        return statuses
    _last_alert[signature] = now

    keys = ", ".join(f"{s.service} ({s.name}): {s.detail}" for s in bad)
    text = t("health_alert", language, keys=keys)

    try:
        await send_telegram(text)
    except Exception as e:
        logger.warning("Health alert: Telegram send failed (%s)", type(e).__name__)

    # A broken Resend key cannot carry its own alert.
    if "RESEND_API_KEY" not in {s.name for s in bad}:
        names = ", ".join(sorted(s.name for s in bad))
        try:
            await asyncio.to_thread(send_email, f"key not accepted: {names}", [text])
        except Exception as e:
            logger.warning("Health alert: email send failed (%s)", type(e).__name__)

    return statuses


async def report_failure(
    send_telegram: SendTelegram,
    send_email: SendEmail,
    *,
    language: str,
    error: str,
    now: float | None = None,
) -> None:
    """After a failed pipeline run: check the keys, else email the exception type. Never raises."""
    try:
        now = time.time() if now is None else now
        statuses = await run_health_check(
            send_telegram, send_email, language=language, reason="failure", now=now
        )
        if problems(statuses):
            return

        if not should_alert(_last_alert, "failure", now, _GAP_FAILURE_SECONDS):
            return
        _last_alert["failure"] = now

        # The type only: the exception message may carry URLs, paths or key fragments.
        error_type = _sanitize(str(error).split(":", 1)[0], "A-Za-z0-9_.", _FAILURE_TYPE_MAX)
        error_type = error_type or "unknown"
        text = t("failure_alert", language, error_type=error_type)
        await asyncio.to_thread(send_email, f"processing failed: {error_type}", [text])
    except Exception as e:
        logger.warning("Failure report did not complete (%s)", type(e).__name__)
