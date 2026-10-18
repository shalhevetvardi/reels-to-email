"""What the sender is told when a reel has no sound, and that they are told once.

Production screenshot, 18 Oct 2026: every failed link got "something went wrong, try
later" twice - once from the pipeline's status callback and once from the bot's own
reply - and "later" never helps for a reel that has no audio. No network, no Telegram.

Tests prefixed test_fix_ fail on the code before this change; test_regression_ ones
guard behaviour that already worked.
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import download  # noqa: E402
import main  # noqa: E402
import pipeline  # noqa: E402
from instagram_post import Post, Slide  # noqa: E402
from messages import MESSAGES, t  # noqa: E402

URL = "https://www.instagram.com/reel/DeXi-IboGbE/"
CHAT = 4242


def _run_video(monkeypatch, failure, callback):
    def failing(url):
        raise failure

    monkeypatch.setattr(pipeline, "download_video", failing)
    return asyncio.run(pipeline._run_video_pipeline(URL, callback, "he"))


# --- the message itself ---------------------------------------------------------------

@pytest.mark.parametrize("language", sorted(MESSAGES))
def test_fix_no_audio_message_exists_and_does_not_say_try_later(language):
    text = t("no_audio", language)
    assert text != "no_audio"  # a missing key falls back to the key's own name
    assert text != t("failed_generic", language)
    assert "later" not in text.lower() and "מאוחר" not in text


def test_fix_hebrew_no_audio_message_names_the_missing_sound():
    assert "שמע" in t("no_audio", "he")


# --- the pipeline ---------------------------------------------------------------------

def test_fix_pipeline_tells_the_sender_the_reel_has_no_sound(monkeypatch):
    seen = []

    async def callback(msg):
        seen.append(msg)

    result = _run_video(monkeypatch, download.NoAudioTrackError("no audio"), callback)
    assert seen == [t("downloading", "he"), t("no_audio", "he")]
    assert result["success"] is False
    assert result["error"].startswith("NoAudioTrackError")
    assert result["user_notified"] is True


def test_fix_pipeline_marks_a_generic_failure_as_already_announced(monkeypatch):
    seen = []

    async def callback(msg):
        seen.append(msg)

    result = _run_video(monkeypatch, RuntimeError("boom"), callback)
    assert seen[-1] == t("failed_generic", "he")
    assert result["user_notified"] is True


def test_fix_without_a_callback_nobody_was_told(monkeypatch):
    result = _run_video(monkeypatch, RuntimeError("boom"), None)
    assert result["success"] is False
    assert result["user_notified"] is False


def test_fix_a_status_message_that_failed_to_send_does_not_count_as_told(monkeypatch):
    async def broken(msg):
        raise RuntimeError("telegram is down")

    result = _run_video(monkeypatch, download.NoAudioTrackError("no audio"), broken)
    assert result["user_notified"] is False


def _failing_carousel(monkeypatch, callback):
    def failing(post, limit):
        raise RuntimeError("no images")

    monkeypatch.setattr(pipeline, "download_slide_images", failing)
    post = Post("ABC", "https://www.instagram.com/p/ABC/", "u", "U", "c", 1, 1,
                (Slide(1, "image", "https://x.fbcdn.net/1.jpg"), Slide(2, "image", "https://x.fbcdn.net/2.jpg")))
    return asyncio.run(pipeline._run_carousel_pipeline(post, callback, "he", None))


def test_fix_carousel_failures_are_marked_as_announced_too(monkeypatch):
    seen = []

    async def callback(msg):
        seen.append(msg)

    result = _failing_carousel(monkeypatch, callback)
    assert seen[-1] == t("failed_generic", "he")
    assert result["success"] is False
    assert result["user_notified"] is True


def test_fix_a_carousel_failure_without_a_callback_told_nobody(monkeypatch):
    assert _failing_carousel(monkeypatch, None)["user_notified"] is False


# --- the bot's own reply --------------------------------------------------------------

def _update(text):
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    return SimpleNamespace(message=message, effective_chat=SimpleNamespace(id=CHAT))


def _context():
    return SimpleNamespace(
        bot=object(), application=SimpleNamespace(create_task=lambda coro: coro.close())
    )


def _handle(monkeypatch, result):
    monkeypatch.setattr(main, "ALLOWED_CHAT_IDS", frozenset({CHAT}))
    monkeypatch.setattr(main, "LANGUAGE", "he")
    monkeypatch.setattr(main.rate_limiter, "allow", lambda chat_id: True)
    monkeypatch.setattr(main, "run_pipeline", AsyncMock(return_value=result))
    monkeypatch.setattr(main, "report_failure", AsyncMock())
    update = _update(URL)
    asyncio.run(main.handle_message(update, _context()))
    return [call.args[0] for call in update.message.reply_text.await_args_list]


def test_fix_a_failure_the_sender_was_already_told_about_gets_no_second_message(monkeypatch):
    replies = _handle(
        monkeypatch, {"success": False, "error": "NoAudioTrackError: x", "user_notified": True}
    )
    assert replies == [t("got_it", "he")]


def test_regression_a_failure_nobody_announced_still_gets_the_generic_reply(monkeypatch):
    replies = _handle(monkeypatch, {"success": False, "error": "RuntimeError: x"})
    assert replies == [t("got_it", "he"), t("failed_generic", "he")]
