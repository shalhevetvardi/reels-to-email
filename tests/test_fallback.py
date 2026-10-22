"""The no-audio fallback: the bot hands a muted reel to a GitHub runner, which runs the
whole pipeline and reports to the sender. No network: GitHub, Telegram and the pipeline
are replaced by stand-ins.

Measured 18 Oct 2026 (the bot ran on Railway then): Instagram mutes reels with a library song for Railway's network
and serves them with sound to GitHub's runners. Tests prefixed test_fix_ fail on the
code before the fallback; test_regression_ ones guard behaviour that already worked.
"""
import asyncio
import io
import json
import logging
import re
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import download  # noqa: E402
import fallback  # noqa: E402
import fallback_job  # noqa: E402
import main  # noqa: E402
import pipeline  # noqa: E402
from messages import t  # noqa: E402

URL = "https://www.instagram.com/reel/DeXi-IboGbE/?cplk=Z2h1ZGxocnR5NDAz"
CHAT = 4242
TOKEN = "placeholder-fallback-token-0001"
WORKFLOW = ROOT / ".github" / "workflows" / fallback.WORKFLOW_FILE


# --- the request to GitHub ------------------------------------------------------------

class _Response:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def github(monkeypatch):
    sent = []

    def fake_urlopen(request, timeout=None):
        sent.append(request)
        return _Response(204)

    monkeypatch.setattr(fallback.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("GITHUB_FALLBACK_TOKEN", TOKEN)
    monkeypatch.delenv("GITHUB_FALLBACK_REPO", raising=False)
    return sent


def test_fix_without_a_token_nothing_is_sent_and_the_answer_is_no(monkeypatch):
    # Counted, not raised: dispatch_fallback swallows exceptions, so a raising stand-in
    # would let a request through and still read as "nothing sent".
    calls = []
    monkeypatch.setattr(fallback.urllib.request, "urlopen", lambda *a, **k: calls.append(a) or _Response(204))
    monkeypatch.delenv("GITHUB_FALLBACK_TOKEN", raising=False)
    assert fallback.dispatch_fallback(URL, CHAT, None) is False
    assert calls == []


def test_fix_the_link_chat_and_note_go_to_the_fallback_workflow(github):
    assert fallback.dispatch_fallback(URL, CHAT, "focus on the hook") is True
    (request,) = github
    assert request.get_method() == "POST"
    assert request.full_url == (
        "https://api.github.com/repos/shalhevetvardi/reels-to-email/actions/workflows/"
        "process-reel-fallback.yml/dispatches")
    assert request.get_header("Authorization") == f"Bearer {TOKEN}"
    assert json.loads(request.data) == {
        "ref": "main", "inputs": {"url": URL, "chat_id": str(CHAT), "note": "focus on the hook"}}


def test_fix_another_repository_can_be_named(github, monkeypatch):
    monkeypatch.setenv("GITHUB_FALLBACK_REPO", "someone/fork")
    fallback.dispatch_fallback(URL, CHAT, None)
    assert "/repos/someone/fork/" in github[0].full_url
    assert json.loads(github[0].data)["inputs"]["note"] == ""


@pytest.mark.parametrize("failure", [
    urllib.error.HTTPError("https://api.github.com", 403, "Forbidden", {}, io.BytesIO(b"")),
    urllib.error.URLError("no network"),
    TimeoutError(),
])
def test_fix_a_refused_or_failed_request_is_no_and_the_token_stays_out_of_the_log(
        monkeypatch, caplog, failure):
    def failing(request, timeout=None):
        raise failure

    monkeypatch.setattr(fallback.urllib.request, "urlopen", failing)
    monkeypatch.setenv("GITHUB_FALLBACK_TOKEN", TOKEN)
    with caplog.at_level(logging.INFO):
        assert fallback.dispatch_fallback(URL, CHAT, None) is False
    assert TOKEN not in caplog.text


# --- the pipeline asks before giving up ------------------------------------------------

def _no_audio_run(monkeypatch, hook):
    def muted(url):
        raise download.NoAudioTrackError("no audio")

    monkeypatch.setattr(pipeline, "download_video", muted)
    seen = []

    async def callback(msg):
        seen.append(msg)

    result = asyncio.run(pipeline._run_video_pipeline(URL, callback, "he", hook))
    return seen, result


def test_fix_a_reel_handed_off_tells_the_sender_where_it_went(monkeypatch):
    seen, result = _no_audio_run(monkeypatch, AsyncMock(return_value=True))
    assert seen[-1] == t("no_audio_fallback", "he")
    assert result["handed_off"] is True
    assert result["user_notified"] is True


@pytest.mark.parametrize("hook", [
    AsyncMock(return_value=False),
    AsyncMock(side_effect=RuntimeError("github is down")),
])
def test_fix_a_reel_nobody_took_gets_the_no_audio_message(monkeypatch, hook):
    seen, result = _no_audio_run(monkeypatch, hook)
    assert seen[-1] == t("no_audio", "he")
    assert result["handed_off"] is False


def test_fix_run_pipeline_passes_the_hook_to_the_video_flow(monkeypatch):
    seen = {}

    async def spy(url, cb, lang, hand_off=None):
        seen["hook"] = hand_off
        return {"success": True}

    monkeypatch.setattr(pipeline, "_run_video_pipeline", spy)
    hook = AsyncMock(return_value=True)
    asyncio.run(pipeline.run_pipeline("https://www.instagram.com/reel/AbC/", hand_off=hook))
    assert seen["hook"] is hook


@pytest.mark.parametrize("language", ["en", "he"])
def test_fix_the_hand_off_message_exists_in_every_language(language):
    text = t("no_audio_fallback", language)
    assert text != "no_audio_fallback"
    assert text != t("no_audio", language)


# --- Instagram's login wall goes the same way ------------------------------------------

def _blocked_run(monkeypatch, hook, failure=None):
    def refused(url):
        raise failure or download.InstagramBlockedError("Instagram refused this server the reel AbC")

    monkeypatch.setattr(pipeline, "download_video", refused)
    seen = []

    async def callback(msg):
        seen.append(msg)

    result = asyncio.run(pipeline._run_video_pipeline(URL, callback, "he", hook))
    return seen, result


def test_fix_a_blocked_reel_is_handed_off_and_the_sender_told(monkeypatch):
    seen, result = _blocked_run(monkeypatch, AsyncMock(return_value=True))
    assert seen[-1] == t("blocked_fallback", "he")
    assert result["handed_off"] is True
    assert result["user_notified"] is True
    assert result["error"].startswith("InstagramBlockedError")


@pytest.mark.parametrize("hook", [
    None,
    AsyncMock(return_value=False),
    AsyncMock(return_value="yes"),  # only a real True counts as taken over
    AsyncMock(side_effect=RuntimeError("github is down")),
])
def test_regression_a_blocked_reel_nobody_took_fails_as_before(monkeypatch, hook):
    seen, result = _blocked_run(monkeypatch, hook)
    assert seen[-1] == t("failed_generic", "he")
    assert not result.get("handed_off")  # so the bot still reports the failure
    assert result["user_notified"] is True


def test_regression_other_failures_are_never_handed_off(monkeypatch):
    hook = AsyncMock(return_value=True)
    seen, result = _blocked_run(monkeypatch, hook, failure=RuntimeError("transcription service down"))
    hook.assert_not_called()
    assert seen[-1] == t("failed_generic", "he")
    assert not result.get("handed_off")


@pytest.mark.parametrize("language", ["en", "he"])
def test_fix_the_blocked_hand_off_message_exists_in_every_language(language):
    text = t("blocked_fallback", language)
    assert text != "blocked_fallback"
    assert text not in (t("no_audio_fallback", language), t("failed_generic", language))


def test_fix_the_blocked_hand_off_message_is_written_in_hebrew():
    # A key missing in Hebrew falls back to English, which the check above cannot see.
    assert t("blocked_fallback", "he") != t("blocked_fallback", "en")
    assert "אינסטגרם" in t("blocked_fallback", "he")


# --- the bot wires the hook to GitHub --------------------------------------------------

def _handle(monkeypatch, result, call_hook=True):
    dispatched = []

    def fake_dispatch(url, chat_id, note):
        dispatched.append((url, chat_id, note))
        return True

    async def fake_pipeline(url, status_callback=None, language="en", user_note=None, hand_off=None):
        if call_hook:
            await hand_off()
        return result

    monkeypatch.setattr(main, "ALLOWED_CHAT_IDS", frozenset({CHAT}))
    monkeypatch.setattr(main, "LANGUAGE", "he")
    monkeypatch.setattr(main.rate_limiter, "allow", lambda chat_id: True)
    monkeypatch.setattr(main, "dispatch_fallback", fake_dispatch)
    monkeypatch.setattr(main, "run_pipeline", fake_pipeline)
    report = AsyncMock()
    monkeypatch.setattr(main, "report_failure", report)
    message = SimpleNamespace(text=f"{URL} focus on the hook", reply_text=AsyncMock())
    update = SimpleNamespace(message=message, effective_chat=SimpleNamespace(id=CHAT))
    context = SimpleNamespace(bot=object(), application=SimpleNamespace(create_task=lambda coro: coro.close()))
    asyncio.run(main.handle_message(update, context))
    return dispatched, report


def test_fix_the_bot_hands_the_link_chat_and_note_to_github(monkeypatch):
    dispatched, report = _handle(
        monkeypatch, {"success": False, "error": "NoAudioTrackError: x", "user_notified": True, "handed_off": True})
    assert len(dispatched) == 1
    url, chat_id, note = dispatched[0]
    assert URL.startswith(url) and chat_id == CHAT and note and "hook" in note
    report.assert_not_called()  # not a failure: the runner reports its own outcome


def test_regression_a_failure_that_was_not_handed_off_is_still_reported(monkeypatch):
    _, report = _handle(monkeypatch, {"success": False, "error": "RuntimeError: x"}, call_hook=False)
    report.assert_called_once()


# --- the job on the GitHub runner ------------------------------------------------------

@pytest.mark.parametrize("url", [
    URL,
    "https://www.instagram.com/reel/DbO1JEoSBE7/?rpxt=N2poNWd4eDJiNTBn",
    "https://instagram.com/p/AbC123/",
    "http://www.instagram.com/share/reel/AbC123",
    "https://instagr.am/tv/AbC123/",
])
def test_fix_the_job_accepts_every_link_shape_the_bot_accepts(url):
    assert main.INSTAGRAM_URL_PATTERN.search(url)  # the bot would pass it on
    assert fallback_job.allowed_chat(url, str(CHAT), f"{CHAT}, 999") == CHAT


@pytest.mark.parametrize("url, chat_id, allowed", [
    (URL, "777", f"{CHAT}"),                                    # not an allowed chat
    (URL, str(CHAT), ""),                                       # no allowlist: nobody
    (URL, str(CHAT), None),
    (URL, "not-a-number", f"{CHAT}"),
    ("https://example.com/reel/AbC/", str(CHAT), f"{CHAT}"),    # not Instagram
    (URL + " && curl evil.example", str(CHAT), f"{CHAT}"),      # more than one link
    ("", str(CHAT), f"{CHAT}"),
])
def test_fix_the_job_refuses_anything_else(url, chat_id, allowed):
    assert fallback_job.allowed_chat(url, chat_id, allowed) is None


def test_fix_the_request_is_read_from_the_dispatch_event(tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"inputs": {"url": URL, "chat_id": str(CHAT), "note": "a\nb"}}), encoding="utf-8")
    assert fallback_job.read_request({"GITHUB_EVENT_PATH": str(event)}) == (URL, str(CHAT), "a\nb")
    assert fallback_job.read_request({"REEL_URL": URL, "CHAT_ID": "1"}) == (URL, "1", "")


def test_fix_every_value_of_the_request_is_masked_line_by_line():
    assert fallback_job.mask_lines(URL, str(CHAT), "first\n\n second ") == [
        f"::add-mask::{URL}", f"::add-mask::{CHAT}", "::add-mask::first", "::add-mask::second"]


@pytest.fixture
def quiet_restore():
    root = logging.getLogger()
    level = root.level
    yield
    root.setLevel(level)
    fallback_job.logger.handlers.clear()
    fallback_job.logger.propagate = True


@pytest.fixture
def no_real_run(monkeypatch):
    async def must_not_run(*a, **k):
        raise AssertionError("the pipeline must not run")

    monkeypatch.setattr(fallback_job, "_run_with_telegram", must_not_run)


def test_fix_a_refused_request_never_runs_the_pipeline(quiet_restore, no_real_run):
    env = {"REEL_URL": URL, "CHAT_ID": "777", "ALLOWED_CHAT_IDS": str(CHAT), "TELEGRAM_BOT_TOKEN": "x"}
    assert fallback_job.main(env) == 2


def test_fix_no_telegram_token_never_runs_the_pipeline(quiet_restore, no_real_run):
    env = {"REEL_URL": URL, "CHAT_ID": str(CHAT), "ALLOWED_CHAT_IDS": str(CHAT)}
    assert fallback_job.main(env) == 2


def test_fix_on_github_the_masks_come_first_and_the_pipeline_log_is_silenced(
        quiet_restore, no_real_run, capsys):
    env = {"GITHUB_ACTIONS": "true", "REEL_URL": URL, "CHAT_ID": "777", "ALLOWED_CHAT_IDS": str(CHAT)}
    fallback_job.main(env)
    lines = capsys.readouterr().out.splitlines()
    assert lines[:2] == [f"::add-mask::{URL}", "::add-mask::777"]
    assert logging.getLogger().level == logging.CRITICAL
    assert not logging.getLogger("pipeline").isEnabledFor(logging.ERROR)


def test_fix_a_successful_run_ends_with_the_email_message(monkeypatch):
    seen_kwargs, sent = {}, []

    async def fake_pipeline(url, **kwargs):
        seen_kwargs.update(kwargs)
        return {"success": True}

    async def send(msg):
        sent.append(msg)

    monkeypatch.setattr(fallback_job, "run_pipeline", fake_pipeline)
    assert asyncio.run(fallback_job.run(URL, "note", "he", send)) == 0
    assert sent == [t("email_arrived", "he")]
    # The runner never hands off again: it passes no hook at all, under any name.
    assert set(seen_kwargs) == {"status_callback", "language", "user_note"}


@pytest.mark.parametrize("result, expected", [
    ({"success": False, "user_notified": True}, []),
    ({"success": False}, ["failed_generic"]),
])
def test_fix_a_failed_run_tells_the_sender_once(monkeypatch, result, expected):
    sent = []

    async def fake_pipeline(url, **kwargs):
        return result

    async def send(msg):
        sent.append(msg)

    monkeypatch.setattr(fallback_job, "run_pipeline", fake_pipeline)
    assert asyncio.run(fallback_job.run(URL, None, "he", send)) == 1
    assert sent == [t(k, "he") for k in expected]


# --- the workflow file -----------------------------------------------------------------

def _workflow():
    return WORKFLOW.read_text(encoding="utf-8")


def test_fix_the_workflow_exists_under_the_name_the_bot_dispatches():
    assert WORKFLOW.is_file()


def test_fix_the_workflow_starts_only_by_dispatch_and_reads_nothing_more():
    text = _workflow()
    on_block = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert re.findall(r"^  (\w+):", on_block, re.M) == ["workflow_dispatch"]
    assert "\npermissions:\n  contents: read\n" in text


def test_fix_the_request_never_reaches_a_step_variable_or_a_command():
    # GitHub prints step variables in the public log, and an input inside a command
    # would let a crafted link run shell code.
    text = _workflow()
    assert "inputs." not in text.split("jobs:", 1)[1]
    assert "github.event" not in text


def test_fix_every_action_is_pinned_to_a_commit():
    uses = re.findall(r"uses:\s*(\S+)", _workflow())
    assert uses and all(re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", u) for u in uses)


def test_fix_the_job_gets_every_secret_the_pipeline_needs():
    names = set(re.findall(r"^\s+([A-Z_]+): \$\{\{ secrets\.\1 \}\}$", _workflow(), re.M))
    assert names == {
        "ALLOWED_CHAT_IDS", "TELEGRAM_BOT_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
        "PERPLEXITY_API_KEY", "RESEND_API_KEY", "RESEND_FROM_EMAIL", "TARGET_EMAIL",
        "PROFILE_CONTENT", "INTERFACE_LANGUAGE"}
    assert "run: python src/fallback_job.py" in _workflow()
