"""A redeploy must not silently cut a link that is being processed. No network, no Telegram."""
import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import main  # noqa: E402
from messages import MESSAGES, t  # noqa: E402

URL = "https://www.instagram.com/reel/AbC123xyz/"
CHAT = 4242
MSG_ID = 777


def _update(text: str = URL, chat: int = CHAT, message_id: int = MSG_ID) -> SimpleNamespace:
    message = SimpleNamespace(text=text, message_id=message_id, reply_text=AsyncMock())
    return SimpleNamespace(message=message, effective_chat=SimpleNamespace(id=chat))


def _in_flight() -> list[tuple]:
    return [(e.chat_id, e.message_id, e.url) for e in main._in_flight]


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(main, "_in_flight", [])
    monkeypatch.setattr(main, "_draining", False)
    monkeypatch.setattr(main, "_drain_task", None)
    monkeypatch.setattr(main, "ALLOWED_CHAT_IDS", frozenset({CHAT}))
    monkeypatch.setattr(main, "LANGUAGE", "he")
    monkeypatch.setattr(main.rate_limiter, "allow", lambda chat_id: True)


def _fake_application(events: list | None = None, send=None):
    events = events if events is not None else []

    async def default_send(**kwargs):
        events.append(("send", kwargs))

    bot = SimpleNamespace(send_message=AsyncMock(side_effect=send or default_send))
    app = SimpleNamespace(
        bot=bot,
        running=True,
        stop_running=Mock(side_effect=lambda: events.append(("stop", None))),
    )
    return app, events


def _signal_and_wait(app, signum=signal.SIGTERM) -> None:
    async def go():
        main._on_stop_signal(app, signum)
        await asyncio.wait_for(main._drain_task, timeout=10)

    asyncio.run(go())


def _register(*entries) -> None:
    for chat, message_id, url in entries:
        main._in_flight.append(main._InFlight(chat, message_id, url))


# --- 1-2: the registry ---


@pytest.mark.parametrize("outcome", ["success", "failure", "raises"])
def test_link_is_registered_while_the_pipeline_runs_and_removed_after(monkeypatch, outcome):
    seen = []

    async def fake_pipeline(url, **kwargs):
        seen.append(_in_flight())
        if outcome == "raises":
            raise RuntimeError("boom")
        return {"success": outcome == "success", "error": "x"}

    monkeypatch.setattr(main, "run_pipeline", fake_pipeline)
    context = SimpleNamespace(application=SimpleNamespace(create_task=lambda coro: coro.close()), bot=None)

    async def go():
        if outcome == "raises":
            with pytest.raises(RuntimeError):
                await main.handle_message(_update(), context)
        else:
            await main.handle_message(_update(), context)

    asyncio.run(go())

    assert seen == [[(CHAT, MSG_ID, URL)]]
    assert _in_flight() == []


def test_unauthorized_chat_never_enters_the_registry(monkeypatch):
    seen = []

    async def fake_pipeline(url, **kwargs):
        seen.append(_in_flight())
        return {"success": True}

    monkeypatch.setattr(main, "run_pipeline", fake_pipeline)
    update = _update(chat=CHAT + 1)
    asyncio.run(main.handle_message(update, SimpleNamespace()))

    assert seen == [] and _in_flight() == []
    update.message.reply_text.assert_not_awaited()


def test_rate_limited_link_never_enters_the_registry(monkeypatch):
    monkeypatch.setattr(main.rate_limiter, "allow", lambda chat_id: False)
    seen = []

    async def fake_pipeline(url, **kwargs):
        seen.append(_in_flight())
        return {"success": True}

    monkeypatch.setattr(main, "run_pipeline", fake_pipeline)
    asyncio.run(main.handle_message(_update(), SimpleNamespace()))

    assert seen == [] and _in_flight() == []


# --- 3-8: the stop signal ---


def test_first_signal_with_one_link_sends_one_notice_then_stops():
    app, events = _fake_application()
    _register((CHAT, MSG_ID, URL))
    _signal_and_wait(app)

    app.bot.send_message.assert_awaited_once()
    kwargs = app.bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == CHAT
    assert kwargs["text"] == t("update_in_progress", "he", url=URL)
    assert URL in kwargs["text"]
    assert kwargs["reply_parameters"].message_id == MSG_ID
    # The sender may have deleted the message since: the notice must still go out.
    assert kwargs["reply_parameters"].allow_sending_without_reply is True
    assert kwargs["link_preview_options"].is_disabled is True
    app.stop_running.assert_called_once()
    assert [kind for kind, _ in events] == ["send", "stop"]


def test_the_stop_log_line_has_no_chat_id_and_no_link(caplog):
    app, _ = _fake_application()
    _register((CHAT, MSG_ID, URL))
    with caplog.at_level(logging.INFO, logger="reels-to-email"):
        _signal_and_wait(app)

    lines = [r.getMessage() for r in caplog.records if "Stop signal" in r.getMessage()]
    assert lines == ["Stop signal SIGTERM received - 1 link(s) in flight."]


def test_each_chat_gets_only_its_own_link():
    other_url = "https://www.instagram.com/reel/Other999/"
    app, events = _fake_application()
    _register((CHAT, MSG_ID, URL), (CHAT + 1, 55, other_url))
    _signal_and_wait(app)

    sent = {c.kwargs["chat_id"]: c.kwargs for c in app.bot.send_message.await_args_list}
    assert len(app.bot.send_message.await_args_list) == 2
    assert URL in sent[CHAT]["text"] and other_url not in sent[CHAT]["text"]
    assert other_url in sent[CHAT + 1]["text"] and URL not in sent[CHAT + 1]["text"]
    assert sent[CHAT + 1]["reply_parameters"].message_id == 55
    app.stop_running.assert_called_once()


def test_first_signal_with_nothing_in_flight_sends_nothing_and_stops(caplog):
    app, _ = _fake_application()
    with caplog.at_level(logging.INFO, logger="reels-to-email"):
        _signal_and_wait(app, signal.SIGINT)

    app.bot.send_message.assert_not_awaited()
    app.stop_running.assert_called_once()
    lines = [r.getMessage() for r in caplog.records if "SIGINT" in r.getMessage()]
    assert len(lines) == 1 and "0 link" in lines[0]


def test_a_failed_notice_does_not_stop_the_others_and_logs_only_the_error_type(caplog):
    sentinel = "SECRET-DETAIL-9f3a"
    calls = []

    async def send(**kwargs):
        calls.append(kwargs["chat_id"])
        if len(calls) == 1:
            raise RuntimeError(sentinel)

    app, _ = _fake_application(send=send)
    _register((CHAT, MSG_ID, URL), (CHAT + 1, 55, "https://www.instagram.com/reel/Other999/"))
    with caplog.at_level(logging.WARNING, logger="reels-to-email"):
        _signal_and_wait(app)

    assert calls == [CHAT, CHAT + 1]
    app.stop_running.assert_called_once()
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("RuntimeError" in w for w in warnings)
    assert not any(sentinel in w for w in warnings)
    assert sentinel not in caplog.text


def test_a_notice_that_never_returns_is_abandoned_after_the_timeout(monkeypatch, caplog):
    monkeypatch.setattr(main, "_NOTICE_TIMEOUT_SECONDS", 0.05)

    async def never(**kwargs):
        await asyncio.Event().wait()

    app, _ = _fake_application(send=never)
    _register((CHAT, MSG_ID, URL))
    started = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="reels-to-email"):
        _signal_and_wait(app)

    assert time.monotonic() - started < 5
    app.stop_running.assert_called_once()
    assert any("TimeoutError" in r.getMessage() for r in caplog.records)


def test_a_second_signal_while_draining_does_nothing(caplog):
    app, _ = _fake_application()
    _register((CHAT, MSG_ID, URL))

    async def go():
        main._on_stop_signal(app, signal.SIGTERM)
        first_task = main._drain_task
        main._on_stop_signal(app, signal.SIGINT)
        assert main._drain_task is first_task
        await main._drain_task

    with caplog.at_level(logging.INFO, logger="reels-to-email"):
        asyncio.run(go())

    app.bot.send_message.assert_awaited_once()
    app.stop_running.assert_called_once()
    assert main._draining is True
    assert any("again" in r.getMessage() and "SIGINT" in r.getMessage() for r in caplog.records)


def test_a_link_that_finished_before_its_turn_gets_no_notice():
    # Several notices can take a while; one for a link whose email already went out would mislead.
    app, _ = _fake_application()
    _register((CHAT, MSG_ID, URL))
    entries = list(main._in_flight)
    main._in_flight.clear()

    async def go():
        await main._announce_and_stop(app, entries)

    asyncio.run(go())
    app.bot.send_message.assert_not_awaited()
    app.stop_running.assert_called_once()


def test_a_signal_during_start_up_ends_the_start_up():
    # Before the application runs, stop_running() only sets a flag that nobody reads any more.
    app, _ = _fake_application()
    app.running = False
    _register()

    async def go():
        main._on_stop_signal(app, signal.SIGTERM)
        await main._drain_task
        await asyncio.sleep(0)

    with pytest.raises(SystemExit):
        asyncio.run(go())
    app.stop_running.assert_called_once()


def test_where_signals_are_unsupported_the_bot_warns_once_and_carries_on(monkeypatch, caplog):
    def refuse(self, *args):
        raise NotImplementedError

    monkeypatch.setattr(asyncio.AbstractEventLoop, "add_signal_handler", refuse, raising=False)
    monkeypatch.setattr(asyncio.SelectorEventLoop, "add_signal_handler", refuse, raising=False)

    async def go():
        main._install_stop_handlers(SimpleNamespace())

    with caplog.at_level(logging.WARNING, logger="reels-to-email"):
        asyncio.run(go())

    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


@pytest.mark.parametrize("error", [RuntimeError, ValueError])
def test_a_refused_handler_never_stops_the_bot_from_starting(monkeypatch, caplog, error):
    def refuse(self, *args):
        raise error("sentinel-detail")

    monkeypatch.setattr(asyncio.AbstractEventLoop, "add_signal_handler", refuse, raising=False)
    monkeypatch.setattr(asyncio.SelectorEventLoop, "add_signal_handler", refuse, raising=False)

    async def go():
        main._install_stop_handlers(SimpleNamespace())

    with caplog.at_level(logging.WARNING, logger="reels-to-email"):
        asyncio.run(go())

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert error.__name__ in warnings[0] and "sentinel-detail" not in warnings[0]


def test_all_three_stop_signals_get_our_handler(monkeypatch):
    seen = []

    def record(self, sig, callback, *args):
        seen.append((sig, callback, args[1]))

    monkeypatch.setattr(asyncio.AbstractEventLoop, "add_signal_handler", record, raising=False)
    monkeypatch.setattr(asyncio.SelectorEventLoop, "add_signal_handler", record, raising=False)
    app = SimpleNamespace()

    async def go():
        main._install_stop_handlers(app)

    asyncio.run(go())

    assert {sig for sig, _, _ in seen} == {signal.SIGTERM, signal.SIGINT, signal.SIGABRT}
    assert all(callback is main._on_stop_signal and passed is sig for sig, callback, passed in seen)


def test_the_librarys_own_stop_handlers_stay_on_during_start_up():
    # With stop_signals=None nothing handles a SIGTERM before _post_init runs, and the process
    # is PID 1 in the container, where an unhandled SIGTERM is ignored.
    calls = []
    app = SimpleNamespace(run_polling=lambda **kwargs: calls.append(kwargs))
    main._run(app)

    assert len(calls) == 1
    assert "stop_signals" not in calls[0]


# --- 9: links that start while draining ---


def test_a_link_that_starts_while_draining_is_acknowledged_with_the_update_note(monkeypatch):
    monkeypatch.setattr(main, "_draining", True)

    async def fake_pipeline(url, **kwargs):
        return {"success": True}

    monkeypatch.setattr(main, "run_pipeline", fake_pipeline)
    update = _update()
    asyncio.run(main.handle_message(update, SimpleNamespace()))

    first = update.message.reply_text.await_args_list[0]
    assert first.args[0] == t("got_it_draining", "he", url=URL)
    assert URL in first.args[0]
    assert first.kwargs["do_quote"] is True
    assert first.kwargs["link_preview_options"].is_disabled is True
    assert update.message.reply_text.await_args_list[-1].args[0] == t("email_arrived", "he")


def test_when_not_draining_the_first_reply_is_the_plain_acknowledgement(monkeypatch):
    async def fake_pipeline(url, **kwargs):
        return {"success": True}

    monkeypatch.setattr(main, "run_pipeline", fake_pipeline)
    update = _update()
    asyncio.run(main.handle_message(update, SimpleNamespace()))

    first = update.message.reply_text.await_args_list[0]
    assert first.args[0] == t("got_it", "he")
    assert "do_quote" not in first.kwargs


# --- 10-11: messages and deploy settings ---


@pytest.mark.parametrize("language", sorted(MESSAGES))
@pytest.mark.parametrize("key", ["update_in_progress", "got_it_draining"])
def test_both_new_messages_name_the_link_in_every_language(language, key):
    assert key in MESSAGES[language]
    text = t(key, language, url=URL)
    assert URL in text
    assert "{url}" not in text and "{" not in text


def test_railway_gives_the_old_container_time_to_finish():
    deploy = json.loads((ROOT / "railway.json").read_text(encoding="utf-8"))["deploy"]
    assert isinstance(deploy["drainingSeconds"], (int, float))
    assert not isinstance(deploy["drainingSeconds"], bool)
    assert deploy["drainingSeconds"] >= 300
    assert deploy["overlapSeconds"] == 0


# --- the real lifecycle, offline ---


def _start_driver(tmp_path, **extra_env):
    log_path = tmp_path / "api-calls.jsonl"
    env = {**os.environ, "STOP_DRIVER_LOG": str(log_path), "PYTHONUNBUFFERED": "1", **extra_env}
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "tests" / "_stop_driver.py")],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines: list[str] = []
    seen = {"STARTUP IN PROGRESS": threading.Event(), "PIPELINE STARTED": threading.Event()}

    def read():
        for line in proc.stdout:
            lines.append(line.rstrip("\n"))
            for marker, event in seen.items():
                if marker in line:
                    event.set()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    return proc, lines, seen, reader, log_path


_NO_ASYNCIO_SIGNALS = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(signal, "SIGTERM"),
    reason="asyncio signal handlers are not available on Windows",
)


def _stop_and_collect(proc, lines, reader) -> tuple[int, str]:
    try:
        try:
            exit_code = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pytest.fail("bot did not exit after SIGTERM:\n" + "\n".join(lines))
        reader.join(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    return exit_code, "\n".join(lines)


def _api_calls(log_path) -> list[dict]:
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


@_NO_ASYNCIO_SIGNALS
def test_real_lifecycle_sigterm_tells_the_sender_and_lets_the_link_finish(tmp_path):
    proc, lines, seen, reader, log_path = _start_driver(tmp_path)
    try:
        assert seen["PIPELINE STARTED"].wait(timeout=30), "pipeline never started:\n" + "\n".join(lines)
        proc.send_signal(signal.SIGTERM)
    finally:
        if proc.poll() is None and not seen["PIPELINE STARTED"].is_set():
            proc.kill()
    exit_code, output = _stop_and_collect(proc, lines, reader)

    sent = [c for c in _api_calls(log_path) if c["method"] == "sendMessage"]
    texts = [c["text"] for c in sent]
    notice = t("update_in_progress", "he", url=URL)
    arrived = t("email_arrived", "he")

    assert exit_code == 0, output
    assert notice in texts, output
    assert arrived in texts, output
    assert texts.index(notice) < texts.index(arrived)
    assert sent[texts.index(notice)]["reply_to"] == 10
    assert "PIPELINE FINISHED" in output
    assert texts.count(notice) == 1


@_NO_ASYNCIO_SIGNALS
def test_real_lifecycle_sigterm_during_start_up_ends_the_process(tmp_path):
    proc, lines, seen, reader, log_path = _start_driver(tmp_path, STOP_DRIVER_SLOW_START="1")
    try:
        assert seen["STARTUP IN PROGRESS"].wait(timeout=30), "\n".join(lines)
        proc.send_signal(signal.SIGTERM)
    finally:
        if proc.poll() is None and not seen["STARTUP IN PROGRESS"].is_set():
            proc.kill()
    exit_code, output = _stop_and_collect(proc, lines, reader)

    assert exit_code == 0, output
    assert "PIPELINE STARTED" not in output, output
    assert not [c for c in _api_calls(log_path) if c["method"] == "sendMessage"]
