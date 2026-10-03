"""send_alert_email: subject contract, escaping, and never raising. No real email."""
import sys
from pathlib import Path

import pytest
import resend

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import email_sender  # noqa: E402
from email_sender import send_alert_email  # noqa: E402

PREFIX = "⚠️ reels-to-email ALERT: "


@pytest.fixture
def sent(monkeypatch):
    payloads = []

    def fake_send(payload):
        payloads.append(payload)
        return {"id": "email-123"}

    monkeypatch.setattr(resend.Emails, "send", fake_send)
    monkeypatch.setenv("TARGET_EMAIL", "owner@example.com")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.delenv("RESEND_FROM_EMAIL", raising=False)
    return payloads


def test_returns_id_and_uses_target_and_default_from(sent):
    assert send_alert_email("key rejected", ["line"]) == "email-123"
    payload = sent[0]
    assert payload["to"] == ["owner@example.com"]
    assert "onboarding@resend.dev" in payload["from"]


def test_from_address_comes_from_env(sent, monkeypatch):
    monkeypatch.setenv("RESEND_FROM_EMAIL", "bot@example.com")
    send_alert_email("x", ["l"])
    assert "bot@example.com" in sent[0]["from"]


def test_subject_has_the_exact_monitoring_literal(sent):
    send_alert_email("key rejected", ["l"])
    subject = sent[0]["subject"]
    assert "reels-to-email ALERT" in subject
    assert subject == PREFIX + "key rejected"


def test_subject_has_no_crlf_even_if_detail_does(sent):
    send_alert_email("first\r\nsecond\nthird\rfourth", ["l"])
    subject = sent[0]["subject"]
    assert "\r" not in subject and "\n" not in subject
    assert "first  second third fourth" in subject


def test_subject_is_truncated(sent):
    send_alert_email("x" * 500, ["l"])
    assert len(sent[0]["subject"]) <= 150 + len(PREFIX)


def test_lines_are_html_escaped_one_paragraph_each(sent):
    send_alert_email("d", ["<b>bold</b> & more", "second"])
    html = sent[0]["html"]
    assert "<b>bold</b>" not in html
    assert "&lt;b&gt;bold&lt;/b&gt; &amp; more" in html
    assert html.count('<p dir="auto"') == 2


def test_returns_none_when_the_send_raises(sent, monkeypatch):
    def boom(payload):
        raise RuntimeError("resend down")

    monkeypatch.setattr(resend.Emails, "send", boom)
    assert send_alert_email("d", ["l"]) is None


@pytest.mark.parametrize("value", [None, "", "PASTE_YOUR_EMAIL_HERE"])
def test_returns_none_when_target_email_is_unset(sent, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("TARGET_EMAIL")
    else:
        monkeypatch.setenv("TARGET_EMAIL", value)
    assert send_alert_email("d", ["l"]) is None
    assert sent == []


def test_failure_log_names_only_the_exception_type(sent, monkeypatch, caplog):
    def boom(payload):
        raise RuntimeError("secret-token-xyz")

    monkeypatch.setattr(resend.Emails, "send", boom)
    with caplog.at_level("WARNING"):
        send_alert_email("d", ["l"])
    assert "RuntimeError" in caplog.text and "secret-token-xyz" not in caplog.text
