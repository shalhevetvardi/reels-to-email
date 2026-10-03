"""What the bot says and logs when the hourly cap skips a link. No network, no Telegram."""
import asyncio
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import main  # noqa: E402
from messages import MESSAGES, t  # noqa: E402

URL = "https://www.instagram.com/reel/AbC123xyz/"
CHAT = 4242


def _update(text: str) -> SimpleNamespace:
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    return SimpleNamespace(message=message, effective_chat=SimpleNamespace(id=CHAT))


@pytest.fixture
def allowed_chat(monkeypatch):
    monkeypatch.setattr(main, "ALLOWED_CHAT_IDS", frozenset({CHAT}))
    monkeypatch.setattr(main, "LANGUAGE", "he")


@pytest.fixture
def over_the_cap(monkeypatch, allowed_chat):
    monkeypatch.setattr(main.rate_limiter, "allow", lambda chat_id: False)

    async def must_not_run(*args, **kwargs):
        raise AssertionError("the pipeline must not run for a skipped link")

    monkeypatch.setattr(main, "run_pipeline", must_not_run)


def test_skipped_link_is_named_in_a_reply_that_quotes_the_message(over_the_cap):
    update = _update(f"תראי את זה {URL}")
    asyncio.run(main.handle_message(update, SimpleNamespace()))

    update.message.reply_text.assert_awaited_once()
    args, kwargs = update.message.reply_text.await_args
    assert URL in args[0]
    assert kwargs.get("do_quote") is True
    assert kwargs["link_preview_options"].is_disabled is True


def test_skipped_link_is_written_to_the_log(over_the_cap, caplog):
    # Words around the link: the log must carry the link itself, not the sender's whole message.
    with caplog.at_level(logging.WARNING, logger="reels-to-email"):
        asyncio.run(main.handle_message(_update(f"look {URL} thanks"), SimpleNamespace()))

    lines = [r.getMessage() for r in caplog.records if "Rate limit reached" in r.getMessage()]
    assert len(lines) == 1
    assert lines[0].endswith(f"skipped {URL}")
    assert "look" not in lines[0] and "thanks" not in lines[0]


def test_link_sent_without_a_scheme_is_logged_with_https(over_the_cap, caplog):
    with caplog.at_level(logging.WARNING, logger="reels-to-email"):
        asyncio.run(main.handle_message(_update("instagram.com/reel/AbC123xyz/"), SimpleNamespace()))

    lines = [r.getMessage() for r in caplog.records if "Rate limit reached" in r.getMessage()]
    assert lines and lines[0].endswith("skipped https://instagram.com/reel/AbC123xyz/")


@pytest.mark.parametrize("cap_allows", [True, False])
def test_an_unknown_chat_gets_no_reply_and_no_run(monkeypatch, cap_allows):
    # The allowlist comes first: someone who is not on it must not learn the bot exists,
    # whether or not the cap would have refused the link.
    monkeypatch.setattr(main, "ALLOWED_CHAT_IDS", frozenset({CHAT + 1}))
    monkeypatch.setattr(main.rate_limiter, "allow", lambda chat_id: cap_allows)

    async def must_not_run(*args, **kwargs):
        raise AssertionError("the pipeline must not run for an unknown chat")

    monkeypatch.setattr(main, "run_pipeline", must_not_run)
    update = _update(URL)
    asyncio.run(main.handle_message(update, SimpleNamespace()))

    update.message.reply_text.assert_not_awaited()


def test_link_sent_without_a_scheme_is_named_with_https(over_the_cap):
    update = _update("instagram.com/reel/AbC123xyz/")
    asyncio.run(main.handle_message(update, SimpleNamespace()))

    args, _ = update.message.reply_text.await_args
    assert "https://instagram.com/reel/AbC123xyz/" in args[0]


def test_each_of_several_skipped_links_gets_its_own_reply(over_the_cap):
    urls = [f"https://www.instagram.com/reel/Link{i}/" for i in range(4)]
    replies = []
    for url in urls:
        update = _update(url)
        asyncio.run(main.handle_message(update, SimpleNamespace()))
        replies.append(update.message.reply_text.await_args.args[0])

    for url, reply in zip(urls, replies):
        assert url in reply
        assert sum(other in reply for other in urls) == 1


def test_an_accepted_link_is_acknowledged_without_a_quote(monkeypatch, allowed_chat):
    monkeypatch.setattr(main.rate_limiter, "allow", lambda chat_id: True)
    seen = []

    async def fake_pipeline(url, **kwargs):
        seen.append(url)
        return {"success": True}

    monkeypatch.setattr(main, "run_pipeline", fake_pipeline)
    update = _update(URL)
    asyncio.run(main.handle_message(update, SimpleNamespace()))

    assert seen == [URL]
    first = update.message.reply_text.await_args_list[0]
    assert first.args[0] == t("got_it", "he")
    assert "do_quote" not in first.kwargs


@pytest.mark.parametrize("language", sorted(MESSAGES))
def test_every_language_names_the_link(language):
    text = t("rate_limited", language, url=URL)
    assert URL in text
    assert "{url}" not in text
