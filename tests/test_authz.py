"""Unit tests for the bot's access control. No network, no Telegram."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from authz import parse_allowed_ids, is_authorized, RateLimiter  # noqa: E402


# --- parse_allowed_ids ---

def test_parse_comma_and_space_separated():
    assert parse_allowed_ids("123, -456 789") == frozenset({123, -456, 789})


def test_parse_empty_or_none_is_empty():
    assert parse_allowed_ids("") == frozenset()
    assert parse_allowed_ids(None) == frozenset()
    assert parse_allowed_ids("   ") == frozenset()


def test_parse_ignores_junk_tokens_does_not_widen():
    # A typo must drop that token, never open the gate.
    assert parse_allowed_ids("123, abc, , 456x, 789") == frozenset({123, 789})


# --- is_authorized (fail-closed) ---

def test_authorized_when_listed():
    allowed = parse_allowed_ids("100 200")
    assert is_authorized(100, allowed) is True
    assert is_authorized(200, allowed) is True


def test_rejected_when_not_listed():
    allowed = parse_allowed_ids("100")
    assert is_authorized(999, allowed) is False


def test_empty_allowlist_rejects_everyone():
    empty = parse_allowed_ids("")
    assert is_authorized(100, empty) is False
    assert is_authorized(0, empty) is False


def test_none_chat_id_rejected():
    assert is_authorized(None, parse_allowed_ids("100")) is False


# --- RateLimiter (deterministic clock via now=) ---

def test_rate_limiter_allows_up_to_cap():
    rl = RateLimiter(max_per_window=3, window_seconds=60)
    assert [rl.allow(1, now=t) for t in (0, 1, 2)] == [True, True, True]


def test_rate_limiter_blocks_over_cap():
    rl = RateLimiter(max_per_window=2, window_seconds=60)
    assert rl.allow(1, now=0) is True
    assert rl.allow(1, now=1) is True
    assert rl.allow(1, now=2) is False  # 3rd within window


def test_rate_limiter_window_slides():
    rl = RateLimiter(max_per_window=1, window_seconds=10)
    assert rl.allow(1, now=0) is True
    assert rl.allow(1, now=5) is False       # still inside window
    assert rl.allow(1, now=11) is True       # old hit expired


def test_rate_limiter_isolates_chats():
    rl = RateLimiter(max_per_window=1, window_seconds=60)
    assert rl.allow(1, now=0) is True
    assert rl.allow(2, now=0) is True        # different chat, own bucket
    assert rl.allow(1, now=1) is False


def test_blocked_attempt_does_not_advance_window():
    # A denied call must not count, or a spammer resets their own window forever.
    rl = RateLimiter(max_per_window=1, window_seconds=10)
    assert rl.allow(1, now=0) is True
    assert rl.allow(1, now=5) is False
    assert rl.allow(1, now=9) is False       # first hit (t=0) still the only counted one
    assert rl.allow(1, now=11) is True       # it expired at 10
