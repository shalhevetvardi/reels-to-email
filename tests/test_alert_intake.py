"""Reports to Yitro's alert intake. No network: httpx.post is replaced."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import alert_intake  # noqa: E402
import health  # noqa: E402
from health import KeyStatus  # noqa: E402


class FakePost:
    def __init__(self, status=202, raises=None):
        self.status, self.raises, self.calls = status, raises, []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.raises:
            raise self.raises
        return type("R", (), {"status_code": self.status})()


@pytest.fixture
def post(monkeypatch):
    fake = FakePost()
    monkeypatch.setattr(alert_intake.httpx, "post", fake)
    monkeypatch.setenv("ALERT_INTAKE_URL", "https://intake.example/")
    monkeypatch.setenv("ALERT_INTAKE_TOKEN", "tkn")
    return fake


def test_names_match_what_the_intake_accepts():
    for check in (alert_intake.CHECK_PROCESSING, alert_intake.CHECK_KEYS):
        assert alert_intake.CHECK_RE.match(check)
    assert alert_intake.SYSTEM_RE.match(alert_intake.SYSTEM)


@pytest.mark.parametrize("env", [{}, {"ALERT_INTAKE_URL": "https://x"}, {"ALERT_INTAKE_TOKEN": "t"},
                                 {"ALERT_INTAKE_URL": " ", "ALERT_INTAKE_TOKEN": "t"}])
def test_not_configured_sends_nothing(monkeypatch, env):
    fake = FakePost()
    monkeypatch.setattr(alert_intake.httpx, "post", fake)
    monkeypatch.delenv("ALERT_INTAKE_URL", raising=False)
    monkeypatch.delenv("ALERT_INTAKE_TOKEN", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert alert_intake.notify("reels.keys", "ok", "x") is False
    assert fake.calls == []


def test_posts_the_report_with_the_senders_token(post):
    assert alert_intake.notify("reels.keys", "fail", "Key not accepted: X") is True
    (url, kw), = post.calls
    assert url == "https://intake.example/v1/alert"
    assert kw["headers"]["Authorization"] == "Bearer tkn"
    assert kw["json"] == {"check": "reels.keys", "status": "fail", "system": "reels-to-email",
                          "summary": "Key not accepted: X"}


def test_long_summary_is_cut_to_the_intakes_limit(post):
    alert_intake.notify("reels.keys", "fail", "a" * 900)
    assert len(post.calls[0][1]["json"]["summary"]) == 500


def test_invalid_report_is_not_sent(post):
    assert alert_intake.notify("bad check!", "fail", "x") is False
    assert alert_intake.notify("reels.keys", "broken", "x") is False
    assert post.calls == []


@pytest.mark.parametrize("fake", [FakePost(status=401), FakePost(raises=RuntimeError("boom"))])
def test_refusal_or_network_error_is_false_and_never_raises(monkeypatch, fake):
    monkeypatch.setattr(alert_intake.httpx, "post", fake)
    monkeypatch.setenv("ALERT_INTAKE_URL", "https://intake.example")
    monkeypatch.setenv("ALERT_INTAKE_TOKEN", "tkn")
    assert alert_intake.notify("reels.keys", "ok", "x") is False


# --- wiring in health.py ---------------------------------------------------------

def _statuses(bad_names):
    return [KeyStatus(name=n, service=n.lower(), state="rejected" if n in bad_names else "ok",
                      detail="refused" if n in bad_names else "")
            for n in ("OPENAI_API_KEY", "RESEND_API_KEY")]


async def _noop(*a, **k):
    return None


def test_health_check_reports_ok_when_every_key_is_accepted(monkeypatch, post):
    monkeypatch.setattr(health, "check_all", lambda: _statuses(set()))
    asyncio.run(health.run_health_check(_noop, lambda *a: None, language="he"))
    assert [c[1]["json"]["status"] for c in post.calls] == ["ok"]
    assert post.calls[0][1]["json"]["check"] == "reels.keys"


def test_health_check_reports_the_refused_key_names(monkeypatch, post):
    monkeypatch.setattr(health, "check_all", lambda: _statuses({"OPENAI_API_KEY"}))
    asyncio.run(health.run_health_check(_noop, lambda *a: None, language="he"))
    body = post.calls[0][1]["json"]
    assert (body["check"], body["status"]) == ("reels.keys", "fail")
    assert "OPENAI_API_KEY" in body["summary"]


def test_failed_link_reports_its_error_type_even_when_the_email_is_throttled(monkeypatch, post):
    monkeypatch.setattr(health, "check_all", lambda: _statuses(set()))
    for _ in range(2):  # the second email is throttled; the report is not
        asyncio.run(health.report_failure(_noop, lambda *a: None, language="he",
                                          error="DownloadError: https://instagram.com/p/x", now=1000.0))
    processing = [c[1]["json"] for c in post.calls if c[1]["json"]["check"] == "reels.processing"]
    assert [p["status"] for p in processing] == ["fail", "fail"]
    assert processing[0]["summary"] == "Processing a link failed: DownloadError"
    assert "instagram" not in processing[0]["summary"]


# --- wiring in main.py -------------------------------------------------------------

def _handled(monkeypatch, success):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import main

    chat = 4242
    monkeypatch.setattr(main, "ALLOWED_CHAT_IDS", frozenset({chat}))
    monkeypatch.setattr(main, "LANGUAGE", "he")
    monkeypatch.setattr(main.rate_limiter, "allow", lambda chat_id: True)

    async def pipeline(*a, **k):
        return {"success": success, "error": "DownloadError: x"}

    monkeypatch.setattr(main, "run_pipeline", pipeline)
    message = SimpleNamespace(text="https://www.instagram.com/reel/AbC123xyz/", reply_text=AsyncMock(),
                              message_id=1)
    update = SimpleNamespace(message=message, effective_chat=SimpleNamespace(id=chat))
    context = SimpleNamespace(bot=None, application=SimpleNamespace(create_task=lambda coro: coro.close()))
    asyncio.run(main.handle_message(update, context))


def test_a_processed_link_closes_the_incident(monkeypatch, post):
    _handled(monkeypatch, success=True)
    assert [(c[1]["json"]["check"], c[1]["json"]["status"]) for c in post.calls] == [
        ("reels.processing", "ok")]


def test_a_failed_link_does_not_report_ok(monkeypatch, post):
    _handled(monkeypatch, success=False)
    assert all(c[1]["json"]["status"] != "ok" for c in post.calls)
