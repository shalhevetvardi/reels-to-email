"""
Access control for the public Telegram bot.

The bot runs a paid pipeline (Whisper, Claude, Perplexity, Resend) for every
Instagram link it receives. The repo is PUBLIC, so without a sender allowlist
anyone who finds the bot could spend the owner's API budget at will.

Two independent guards, both fail-closed:

  1. Allowlist  — only chat ids listed in ALLOWED_CHAT_IDS may trigger anything.
                  An unset/empty allowlist rejects EVERYONE (never allow-all).
  2. Rate limit — even an allowed sender is capped to N pipeline runs per window,
                  as a backstop against a compromised account or a loop.

Everything here is pure and import-safe (no Telegram, no I/O) so it is unit
tested directly.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Deque, Dict, FrozenSet, Iterable


def parse_allowed_ids(raw: str | None) -> FrozenSet[int]:
    """Parse ALLOWED_CHAT_IDS into a set of ints.

    Accepts comma- and/or whitespace-separated integers, e.g. "123, -456 789".
    Blanks and non-integer tokens are ignored (a typo must never silently widen
    access — it just drops that token). Returns an empty set when nothing valid
    is present, which the caller treats as "reject everyone".
    """
    if not raw:
        return frozenset()
    ids = set()
    for token in raw.replace(",", " ").split():
        token = token.strip()
        if not token:
            continue
        try:
            ids.add(int(token))
        except ValueError:
            # Ignore junk rather than fail open or crash the bot on a typo.
            continue
    return frozenset(ids)


def is_authorized(chat_id: int | None, allowed: FrozenSet[int]) -> bool:
    """True only if chat_id is explicitly listed.

    Fail-closed by construction: an empty `allowed` set can never contain
    anything, so an unconfigured allowlist rejects every sender.
    """
    if chat_id is None:
        return False
    return chat_id in allowed


class RateLimiter:
    """In-memory sliding-window limiter, keyed by chat id.

    Single replica (numReplicas=1 in railway.json), so in-process state is
    sufficient; a restart resets counters, which is acceptable for a backstop.
    """

    def __init__(self, max_per_window: int, window_seconds: float) -> None:
        if max_per_window < 1:
            raise ValueError("max_per_window must be >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self.max_per_window = max_per_window
        self.window_seconds = float(window_seconds)
        self._hits: Dict[int, Deque[float]] = defaultdict(deque)

    def allow(self, chat_id: int, now: float | None = None) -> bool:
        """Record an attempt and return whether it is within the limit.

        Returns True and counts the hit when under the cap; returns False and
        does NOT count it when the cap is already reached (so a blocked caller
        cannot push the window forward and starve themselves indefinitely).
        """
        now = time.monotonic() if now is None else now
        bucket = self._hits[chat_id]
        cutoff = now - self.window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= self.max_per_window:
            return False
        bucket.append(now)
        return True
