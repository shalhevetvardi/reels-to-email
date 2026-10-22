"""Webhook mode: which mode runs, that it refuses to start unsafe, and that a repeat is ignored.

No network: run_webhook is replaced by a recorder, and the lifecycle test in
test_webhook_lifecycle.py talks to the loopback only.
"""
import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from telegram import Update
from telegram.ext import ApplicationHandlerStop, TypeHandler

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import main  # noqa: E402
from webhook_mode import (  # noqa: E402
    MIN_SECRET_LENGTH,
    SeenUpdates,
    WebhookConfigError,
    derive_url_path,
    load_webhook_config,
)

SECRET = "s3cret_" + "a" * 40
BASE = "https://reels-bot.onrender.com"


def _env(**overrides):
    env = {"TELEGRAM_WEBHOOK_SECRET": SECRET, "RENDER_EXTERNAL_URL": BASE, "PORT": "10000"}
    env.update(overrides)
    return {k: v for k, v in env.items() if v is not None}


# --- mode selection ---


def test_nothing_set_means_polling():
    assert load_webhook_config({}) is None


def test_render_address_and_port_alone_do_not_select_webhook_mode():
    # Render sets both on every web service; only the secret chooses the mode.
    assert load_webhook_config({"RENDER_EXTERNAL_URL": BASE, "PORT": "10000"}) is None


def test_secret_selects_webhook_mode_with_the_derived_path_and_address():
    config = load_webhook_config(_env())
    path = derive_url_path(SECRET)

    assert config.secret_token == SECRET
    assert config.url_path == path
    assert config.webhook_url == f"{BASE}/{path}"
    assert config.port == 10000
    assert config.listen == "0.0.0.0"


def test_the_explicit_base_url_wins_over_the_render_one_and_loses_its_trailing_slash():
    config = load_webhook_config(_env(TELEGRAM_WEBHOOK_BASE_URL="https://tunnel.example.test/"))
    assert config.webhook_url == f"https://tunnel.example.test/{derive_url_path(SECRET)}"


def test_the_path_cannot_be_guessed_from_the_secret_or_turned_back_into_it():
    path = derive_url_path(SECRET)
    assert re.fullmatch(r"[0-9a-f]{64}", path)
    assert SECRET not in path
    assert derive_url_path(SECRET) == path
    assert derive_url_path(SECRET + "x") != path


def test_the_config_hides_the_secret_and_the_path_from_repr_and_logs():
    config = load_webhook_config(_env())
    text = repr(config)
    assert SECRET not in text and config.url_path not in text and BASE not in text


# --- fail closed ---


@pytest.mark.parametrize(
    "env, fragment",
    [
        # A base URL without a secret is half a webhook setup, not "polling".
        ({"TELEGRAM_WEBHOOK_BASE_URL": BASE, "PORT": "10000"}, "TELEGRAM_WEBHOOK_SECRET"),
        (_env(TELEGRAM_WEBHOOK_SECRET=""), "TELEGRAM_WEBHOOK_SECRET"),
        (_env(TELEGRAM_WEBHOOK_SECRET="   "), "TELEGRAM_WEBHOOK_SECRET"),
        (_env(TELEGRAM_WEBHOOK_SECRET="a" * (MIN_SECRET_LENGTH - 1)), "shorter"),
        (_env(TELEGRAM_WEBHOOK_SECRET="a" * 16 + "!" * 20), "A-Z"),
        (_env(TELEGRAM_WEBHOOK_SECRET="a" * 257), "at most"),
        (_env(RENDER_EXTERNAL_URL=None), "public address"),
        (_env(RENDER_EXTERNAL_URL="http://reels-bot.onrender.com"), "https://"),
        (_env(PORT=None), "PORT"),
        (_env(PORT="abc"), "PORT"),
        (_env(PORT="0"), "PORT"),
        (_env(PORT="70000"), "PORT"),
    ],
)
def test_an_unsafe_webhook_setup_refuses_to_start(env, fragment):
    with pytest.raises(WebhookConfigError) as error:
        load_webhook_config(env)
    assert fragment in str(error.value)


def test_a_secret_of_exactly_the_minimum_length_is_accepted():
    assert load_webhook_config(_env(TELEGRAM_WEBHOOK_SECRET="a" * MIN_SECRET_LENGTH)) is not None


@pytest.mark.parametrize("bad", ["a" * 16 + "!" * 20, "a" * 20, "b" * 300])
def test_the_error_never_contains_the_secret(bad):
    with pytest.raises(WebhookConfigError) as error:
        load_webhook_config(_env(TELEGRAM_WEBHOOK_SECRET=bad))
    assert bad not in str(error.value)


def test_a_short_secret_ends_the_process_with_a_clear_message_and_a_failing_exit_code(tmp_path):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "TELEGRAM_WEBHOOK_SECRET": "too-short-" + "z" * 5,
        "RENDER_EXTERNAL_URL": BASE,
        "PORT": "10000",
    }
    result = subprocess.run(
        [sys.executable, str(ROOT / "src" / "main.py")],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode != 0
    assert "TELEGRAM_WEBHOOK_SECRET" in result.stderr and "shorter" in result.stderr
    assert "z" * 5 not in result.stderr + result.stdout


# --- main() picks the mode, and _run hands the settings to PTB ---


@pytest.fixture
def entry_point(monkeypatch):
    for name in ("TELEGRAM_WEBHOOK_SECRET", "TELEGRAM_WEBHOOK_BASE_URL", "RENDER_EXTERNAL_URL", "PORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(main, "TELEGRAM_BOT_TOKEN", "123456:TEST")
    monkeypatch.setattr(main, "_build_application", lambda: "the-application")
    runs = []
    monkeypatch.setattr(main, "_run", lambda app, webhook=None: runs.append((app, webhook)))
    return runs


def test_main_polls_when_no_webhook_setting_exists(entry_point):
    main.main()
    assert entry_point == [("the-application", None)]


def test_main_runs_the_webhook_when_the_secret_is_set(entry_point, monkeypatch):
    for key, value in _env().items():
        monkeypatch.setenv(key, value)
    main.main()

    (app, webhook), = entry_point
    assert app == "the-application"
    assert webhook.secret_token == SECRET and webhook.url_path == derive_url_path(SECRET)


def test_main_refuses_a_short_secret_before_building_or_running_anything(entry_point, monkeypatch):
    monkeypatch.setattr(main, "_build_application", lambda: pytest.fail("built the application"))
    for key, value in _env(TELEGRAM_WEBHOOK_SECRET="short").items():
        monkeypatch.setenv(key, value)

    with pytest.raises(SystemExit) as stop:
        main.main()

    assert stop.value.code and "shorter" in str(stop.value.code)
    assert entry_point == []


def test_run_webhook_gets_the_secret_token_the_derived_path_and_the_address():
    config = load_webhook_config(_env())
    app = SimpleNamespace(run_webhook=Mock(), run_polling=Mock())

    main._run(app, config)

    app.run_polling.assert_not_called()
    app.run_webhook.assert_called_once()
    kwargs = app.run_webhook.call_args.kwargs
    assert kwargs["secret_token"] == SECRET
    assert kwargs["url_path"] == derive_url_path(SECRET)
    assert kwargs["webhook_url"] == f"{BASE}/{derive_url_path(SECRET)}"
    assert kwargs["port"] == 10000
    assert kwargs["listen"] == "0.0.0.0"
    assert kwargs["allowed_updates"] == Update.ALL_TYPES
    # The library's own stop handlers stay on, as in polling.
    assert "stop_signals" not in kwargs


def test_without_a_webhook_setting_run_polls_and_never_opens_a_webhook():
    app = SimpleNamespace(run_webhook=Mock(), run_polling=Mock())
    main._run(app)
    app.run_webhook.assert_not_called()
    app.run_polling.assert_called_once_with(allowed_updates=Update.ALL_TYPES)


# --- the repeat guard ---


def test_a_repeated_update_id_is_ignored():
    seen = SeenUpdates()
    assert seen.first_time(1, chat_id=7, message_id=10) is True
    assert seen.first_time(1, chat_id=7, message_id=10) is False
    assert seen.first_time(1) is False


def test_the_same_message_under_a_new_update_id_is_ignored():
    seen = SeenUpdates()
    assert seen.first_time(1, chat_id=7, message_id=10) is True
    assert seen.first_time(2, chat_id=7, message_id=10) is False
    assert seen.first_time(3, chat_id=7, message_id=11) is True
    assert seen.first_time(4, chat_id=8, message_id=10) is True


def test_updates_without_a_message_are_told_apart_by_update_id_only():
    # An edit keeps the message id: the handler passes no message for it, so it is not a repeat.
    seen = SeenUpdates()
    assert seen.first_time(1, chat_id=7, message_id=10) is True
    assert seen.first_time(2) is True


def test_the_memory_is_bounded_and_forgets_the_oldest_first():
    seen = SeenUpdates(limit=1000)
    for update_id in range(1, 1002):
        assert seen.first_time(update_id) is True

    assert seen.first_time(1001) is False
    assert seen.first_time(2) is False
    assert seen.first_time(1) is True  # pushed out by the 1001st: the cap held


def _incoming(update_id, chat_id=4242, message_id=10):
    message = SimpleNamespace(chat_id=chat_id, message_id=message_id)
    return SimpleNamespace(update_id=update_id, message=message)


def test_the_handler_lets_an_update_through_once_and_stops_the_repeat(monkeypatch):
    monkeypatch.setattr(main, "_seen_updates", SeenUpdates())

    asyncio.run(main._drop_repeated_update(_incoming(5), None))
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(main._drop_repeated_update(_incoming(5), None))
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(main._drop_repeated_update(_incoming(6), None))  # same message, new update id
    asyncio.run(main._drop_repeated_update(_incoming(7, message_id=11), None))


def test_the_handler_logs_no_chat_id_message_id_or_update_id(monkeypatch, caplog):
    monkeypatch.setattr(main, "_seen_updates", SeenUpdates())
    asyncio.run(main._drop_repeated_update(_incoming(918273, chat_id=555111, message_id=424242), None))
    with caplog.at_level("INFO", logger="reels-to-email"):
        with pytest.raises(ApplicationHandlerStop):
            asyncio.run(main._drop_repeated_update(_incoming(918273, 555111, 424242), None))

    assert "delivered again" in caplog.text
    for value in ("918273", "555111", "424242"):
        assert value not in caplog.text


def test_the_guard_is_the_first_handler_group_of_the_application(monkeypatch):
    monkeypatch.setattr(main, "TELEGRAM_BOT_TOKEN", "123456:TEST")
    app = main._build_application()

    first_group = min(app.handlers)
    assert first_group < 0
    (guard,) = app.handlers[first_group]
    assert isinstance(guard, TypeHandler) and guard.callback is main._drop_repeated_update
    # The real handlers all run in a later group, so ApplicationHandlerStop reaches them.
    assert all(group > first_group for group in app.handlers if group != first_group)
