"""Carousel email rendering and sending. No network, no real email."""
import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import email_sender  # noqa: E402
from email_sender import render_carousel_email, send_carousel_email  # noqa: E402

FIELDS = dict(
    instagram_url="https://www.instagram.com/p/ABC/",
    author="someuser",
    slide_count=3,
    content="what it says",
    structure="how it is built",
    brief="the brief",
    research="the research",
    relevant=True,
    reason="because",
)


def render(**overrides):
    return render_carousel_email(**{**FIELDS, **overrides})


def test_all_card_titles_present():
    _, html = render()
    for title in ("תוכן הקרוסלה", "איך הקרוסלה בנויה", "בריף לשחזור - להעתקה לסוכן AI", "מחקר והרחבה"):
        assert title in html


def test_script_in_content_is_stripped():
    _, html = render(content="hello <script>alert(1)</script> world")
    assert "<script" not in html.lower()


def test_llm_fields_are_sanitized_everywhere():
    evil = '<img src=x onerror="alert(1)"><script>bad()</script>'
    _, html = render(content=evil, structure=evil, brief=evil, research=evil, reason=evil)
    assert "onerror" not in html.lower()
    assert "<script" not in html.lower()


def test_author_is_escaped():
    _, html = render(author="<b>x</b>")
    assert "<b>x</b>" not in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html


def test_subject_prefix_and_no_newlines():
    subject, _ = render(content="## heading\n\ntext\r\nmore")
    assert subject.startswith("[Reels] 🎠")
    assert "\n" not in subject and "\r" not in subject
    assert "#" not in subject
    # The heading line is skipped: the subject is built from the prose.
    assert "text more" in subject and "heading" not in subject


def test_subject_marks_and_truncation():
    subject, _ = render(content="word " * 40, relevant=False)
    assert subject.startswith("[Reels] 🎠 ⏭ @someuser: ")
    assert subject.endswith("...")
    assert len(subject) <= len("[Reels] 🎠 ⏭ @someuser: ") + 73


def test_brief_card_absent_when_empty():
    _, html = render(brief="  \n")
    assert "בריף לשחזור" not in html
    assert "אפשר להעתיק את החלק הזה" not in html


def test_research_card_absent_when_empty():
    _, html = render(research="")
    assert "מחקר והרחבה" not in html


def test_slides_strip_only_with_cids():
    _, without = render()
    assert "השקפים" not in without and "cid:" not in without
    _, with_cids = render(image_cids=["slide-01", "slide-02"])
    assert "השקפים" in with_cids
    assert 'src="cid:slide-01"' in with_cids and 'src="cid:slide-02"' in with_cids
    assert 'alt="slide 2"' in with_cids


# --- sending ---

@pytest.fixture
def sent(monkeypatch):
    monkeypatch.setenv("TARGET_EMAIL", "me@example.com")
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    calls = []

    def fake_send(params):
        calls.append(params)
        return {"id": "email-1"}

    monkeypatch.setattr(email_sender.resend.Emails, "send", fake_send)
    return calls


def test_send_attaches_images_with_content_ids(sent):
    images = [("slide-01.jpg", b"aaa", "image/jpeg"), ("slide-02.png", b"bbb", "image/png")]
    email_id = send_carousel_email(**FIELDS, images=images)
    assert email_id == "email-1"
    assert len(sent) == 1
    atts = sent[0]["attachments"]
    assert [a["content_id"] for a in atts] == ["slide-01", "slide-02"]
    assert atts[0]["content"] == base64.b64encode(b"aaa").decode()
    assert atts[1]["content_type"] == "image/png"
    assert 'src="cid:slide-01"' in sent[0]["html"]
    assert sent[0]["to"] == ["me@example.com"]


def _resend_error(error_type):
    return email_sender.resend.exceptions.ResendError(
        code=422, error_type=error_type, message="m", suggested_action="a"
    )


def test_send_retries_without_attachments_when_the_server_refuses(monkeypatch):
    monkeypatch.setenv("TARGET_EMAIL", "me@example.com")
    calls = []

    def flaky(params):
        calls.append(params)
        if len(calls) == 1:
            raise _resend_error("validation_error")
        return {"id": "email-2"}

    monkeypatch.setattr(email_sender.resend.Emails, "send", flaky)
    email_id = send_carousel_email(**FIELDS, images=[("slide-01.jpg", b"aaa", "image/jpeg")])
    assert email_id == "email-2"
    assert len(calls) == 2
    assert "attachments" in calls[0]
    assert "attachments" not in calls[1]
    assert "cid:" not in calls[1]["html"]


@pytest.mark.parametrize("error", ["network", "plain"])
def test_send_is_not_retried_when_the_first_email_may_have_gone_out(monkeypatch, error):
    monkeypatch.setenv("TARGET_EMAIL", "me@example.com")
    calls = []

    def unclear(params):
        calls.append(params)
        raise _resend_error("HttpClientError") if error == "network" else TimeoutError("no answer")

    monkeypatch.setattr(email_sender.resend.Emails, "send", unclear)
    with pytest.raises(Exception):
        send_carousel_email(**FIELDS, images=[("slide-01.jpg", b"aaa", "image/jpeg")])
    assert len(calls) == 1


def test_send_without_images_failure_is_raised(monkeypatch):
    monkeypatch.setenv("TARGET_EMAIL", "me@example.com")

    def always_fail(params):
        raise RuntimeError("down")

    monkeypatch.setattr(email_sender.resend.Emails, "send", always_fail)
    with pytest.raises(RuntimeError):
        send_carousel_email(**FIELDS)


def test_send_caps_total_attachment_size(sent, monkeypatch):
    monkeypatch.setattr(email_sender, "_MAX_ATTACHMENT_BYTES", 10)
    images = [("a.jpg", b"x" * 6, "image/jpeg"), ("b.jpg", b"x" * 6, "image/jpeg")]
    send_carousel_email(**FIELDS, images=images)
    assert [a["content_id"] for a in sent[0]["attachments"]] == ["slide-01"]


def test_send_requires_target_email(monkeypatch):
    monkeypatch.delenv("TARGET_EMAIL", raising=False)
    with pytest.raises(RuntimeError):
        send_carousel_email(**FIELDS)


def test_subject_skips_heading_and_names_author():
    subject, _ = render_carousel_email(
        instagram_url="https://www.instagram.com/p/ABC/",
        author="some.creator",
        slide_count=3,
        content="### About\n\nThe carousel shows three proof screenshots.",
        structure="s",
        brief="b",
        research="",
        relevant=True,
        reason="r",
    )
    assert subject.startswith("[Reels] 🎠 ✓ @some.creator: The carousel shows")
    assert "About" not in subject


def test_subject_author_is_reduced_to_handle_characters():
    subject, _ = render_carousel_email(
        instagram_url="https://www.instagram.com/p/ABC/",
        author="evil\r\nBcc: x@y.z <b>",
        slide_count=1,
        content="text",
        structure="s",
        brief="",
        research="",
        relevant=False,
        reason="r",
    )
    assert "\r" not in subject and "\n" not in subject
    assert "<" not in subject and " Bcc" not in subject
