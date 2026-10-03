"""Key self-check and alerts. No network, no email, no Telegram."""
import asyncio
import logging
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import health  # noqa: E402
from health import KeyStatus  # noqa: E402

SENTINEL = "sk-SENTINEL-123"
ENV_NAMES = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "PERPLEXITY_API_KEY", "RESEND_API_KEY")


class FakeResponse:
    def __init__(self, status_code, body=None, *, bad_json=False):
        self.status_code = status_code
        self._body = body
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._body


class FakeHttp:
    """Replaces httpx.request: records calls, answers per URL (or raises)."""

    def __init__(self, answers):
        self.answers = answers  # url fragment -> FakeResponse | Exception
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for fragment, answer in self.answers.items():
            if fragment in url:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise AssertionError(f"unexpected request to {url}")


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    health._last_alert.clear()
    for name in ENV_NAMES:
        monkeypatch.setenv(name, SENTINEL)
    monkeypatch.delenv("HEALTH_CHECK_INTERVAL_HOURS", raising=False)
    yield
    health._last_alert.clear()


def install(monkeypatch, answers):
    fake = FakeHttp(answers)
    monkeypatch.setattr(health.httpx, "request", fake)
    return fake


def check_one(name):
    return next(s for s in health.check_all() if s.name == name)


# --- per provider: status table ---

PROVIDERS = {
    "OPENAI_API_KEY": ("api.openai.com", "OpenAI", [200], [401, 403]),
    "ANTHROPIC_API_KEY": ("api.anthropic.com", "Anthropic", [200], [401, 403]),
    "PERPLEXITY_API_KEY": ("api.perplexity.ai", "Perplexity", [400, 422], [401, 403]),
}


@pytest.mark.parametrize("name", PROVIDERS)
def test_ok_statuses(monkeypatch, name):
    fragment, service, oks, _ = PROVIDERS[name]
    for code in oks:
        install(monkeypatch, {fragment: FakeResponse(code, {})})
        status = check_one(name)
        assert (status.state, status.service) == ("ok", service)


@pytest.mark.parametrize("name", PROVIDERS)
def test_rejected_statuses(monkeypatch, name):
    fragment, _, _, rejected = PROVIDERS[name]
    for code in rejected:
        install(monkeypatch, {fragment: FakeResponse(code, {"error": {"type": "invalid_api_key"}})})
        status = check_one(name)
        assert status.state == "rejected"
        assert str(code) in status.detail and "invalid_api_key" in status.detail


@pytest.mark.parametrize("name", [*PROVIDERS, "RESEND_API_KEY"])
def test_server_error_and_unexpected_status_are_unknown(monkeypatch, name):
    fragment = PROVIDERS[name][0] if name in PROVIDERS else "api.resend.com"
    for code in (500, 503, 429, 404):
        install(monkeypatch, {fragment: FakeResponse(code, {})})
        assert check_one(name).state == "unknown"


@pytest.mark.parametrize("name", [*PROVIDERS, "RESEND_API_KEY"])
def test_connection_error_and_timeout_are_unknown(monkeypatch, name):
    fragment = PROVIDERS[name][0] if name in PROVIDERS else "api.resend.com"
    for exc in (httpx.ConnectError("boom"), httpx.ReadTimeout("slow"), RuntimeError(f"failed with {SENTINEL} in the text")):
        install(monkeypatch, {fragment: exc})
        status = check_one(name)
        assert status.state == "unknown"
        assert SENTINEL not in status.detail
        assert "SENTINEL" not in status.detail and "failed" not in status.detail


HOSTS = {
    "OPENAI_API_KEY": "api.openai.com",
    "ANTHROPIC_API_KEY": "api.anthropic.com",
    "PERPLEXITY_API_KEY": "api.perplexity.ai",
    "RESEND_API_KEY": "api.resend.com",
}


@pytest.mark.parametrize("name", ENV_NAMES)
@pytest.mark.parametrize("blank", ["", "   ", None])
def test_blank_key_is_missing_and_makes_no_request(monkeypatch, name, blank):
    # The other three keys are still checked, so the fake answers for them only.
    answers = {h: FakeResponse(200, {}) for key, h in HOSTS.items() if key != name}
    fake = install(monkeypatch, answers)
    if blank is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, blank)
    assert check_one(name).state == "missing"
    assert all(HOSTS[name] not in url for _, url, _ in fake.calls)


def test_all_blank_makes_zero_requests(monkeypatch):
    fake = install(monkeypatch, {})
    for name in ENV_NAMES:
        monkeypatch.setenv(name, "")
    assert [s.state for s in health.check_all()] == ["missing"] * 4
    assert fake.calls == []


# --- Resend ---

def test_resend_200_ok(monkeypatch):
    install(monkeypatch, {"api.resend.com": FakeResponse(200, {"data": []})})
    assert check_one("RESEND_API_KEY").state == "ok"


def test_resend_restricted_key_is_ok(monkeypatch):
    install(monkeypatch, {"api.resend.com": FakeResponse(401, {"name": "restricted_api_key"})})
    assert check_one("RESEND_API_KEY").state == "ok"


def test_resend_invalid_key_is_rejected(monkeypatch):
    install(monkeypatch, {"api.resend.com": FakeResponse(401, {"name": "invalid_api_key"})})
    status = check_one("RESEND_API_KEY")
    assert status.state == "rejected" and "invalid_api_key" in status.detail


def test_resend_validation_error_is_rejected(monkeypatch):
    install(monkeypatch, {"api.resend.com": FakeResponse(400, {"name": "validation_error"})})
    assert check_one("RESEND_API_KEY").state == "rejected"


def test_resend_403_is_rejected(monkeypatch):
    install(monkeypatch, {"api.resend.com": FakeResponse(403, {"name": "forbidden"})})
    assert check_one("RESEND_API_KEY").state == "rejected"


def test_resend_401_with_unreadable_body_is_rejected(monkeypatch):
    install(monkeypatch, {"api.resend.com": FakeResponse(401, bad_json=True)})
    assert check_one("RESEND_API_KEY").state == "rejected"


# --- request shape ---

def test_requests_are_read_only_safe_and_authenticated(monkeypatch):
    fake = install(monkeypatch, {
        "api.openai.com": FakeResponse(200, {}),
        "api.anthropic.com": FakeResponse(200, {}),
        "api.perplexity.ai": FakeResponse(400, {}),
        "api.resend.com": FakeResponse(200, {}),
    })
    statuses = health.check_all()
    assert [s.name for s in statuses] == list(ENV_NAMES)
    by_host = {url.split("/")[2]: (method, url, kw) for method, url, kw in fake.calls}
    assert by_host["api.openai.com"][:2] == ("GET", "https://api.openai.com/v1/models")
    assert by_host["api.anthropic.com"][2]["headers"]["x-api-key"] == SENTINEL
    assert by_host["api.anthropic.com"][2]["headers"]["anthropic-version"] == "2023-06-01"
    method, url, kw = by_host["api.perplexity.ai"]
    assert (method, url) == ("POST", "https://api.perplexity.ai/chat/completions")
    assert kw["json"] == {}
    assert by_host["api.resend.com"][1] == "https://api.resend.com/domains"
    for _, _, kw in fake.calls:
        assert kw["timeout"] == 20 and kw["follow_redirects"] is False
        assert kw["headers"]["User-Agent"].startswith("Mozilla/")


# --- secrecy and sanitizing ---

def test_key_never_in_detail_alert_or_logs(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    # A hostile provider body that echoes the key everywhere except the type field.
    body = {"error": {"type": "invalid_api_key", "message": f"bad key {SENTINEL}"}}
    install(monkeypatch, {host: FakeResponse(401, body) for host in
                          ("api.openai.com", "api.anthropic.com", "api.perplexity.ai")}
            | {"api.resend.com": FakeResponse(401, {"name": "invalid_api_key", "message": SENTINEL})})
    sent = []

    async def tg(text):
        sent.append(text)

    emails = []
    statuses = asyncio.run(health.run_health_check(
        tg, lambda detail, lines: emails.append((detail, list(lines))), language="he", now=1000.0))
    assert statuses and all(s.state == "rejected" for s in statuses)
    for s in statuses:
        assert SENTINEL not in s.detail and SENTINEL not in repr(s)
    assert sent and all(SENTINEL not in text for text in sent)
    assert all(SENTINEL not in detail and SENTINEL not in " ".join(lines) for detail, lines in emails)
    assert SENTINEL not in caplog.text


def test_detail_is_sanitized_and_capped(monkeypatch):
    install(monkeypatch, {"api.openai.com": FakeResponse(401, {"error": {"type": "invalid<script>"}})})
    detail = check_one("OPENAI_API_KEY").detail
    assert "<" not in detail and ">" not in detail
    assert "invalidscript" in detail

    long_type = "a" * 200
    install(monkeypatch, {"api.openai.com": FakeResponse(401, {"error": {"code": long_type}})})
    assert len(check_one("OPENAI_API_KEY").detail) <= 40


@pytest.mark.parametrize("body,expected", [
    ({"error": {"type": "authentication_error"}}, "authentication_error"),
    ({"error": {"code": "invalid_api_key"}}, "invalid_api_key"),
    ({"name": "invalid_api_key"}, "invalid_api_key"),
    ({"error": "just a string with a secret"}, ""),
    ({"message": "no type here"}, ""),
    (["not", "a", "dict"], ""),
])
def test_error_type_extraction(monkeypatch, body, expected):
    install(monkeypatch, {"api.openai.com": FakeResponse(401, body)})
    detail = check_one("OPENAI_API_KEY").detail
    assert detail == ("HTTP 401 " + expected).strip()


# --- pure helpers ---

def _status(name, state):
    return KeyStatus(name, name.split("_")[0].title(), state, "HTTP 401")


def test_problems_selects_rejected_and_missing():
    statuses = [_status("A_KEY", "ok"), _status("B_KEY", "rejected"),
                _status("C_KEY", "missing"), _status("D_KEY", "unknown")]
    assert [s.name for s in health.problems(statuses)] == ["B_KEY", "C_KEY"]


def test_should_alert_never_sent_gap_not_passed_gap_passed():
    last = {}
    assert health.should_alert(last, "sig", 1000.0, 100.0) is True
    last["sig"] = 1000.0
    assert health.should_alert(last, "sig", 1099.0, 100.0) is False
    assert health.should_alert(last, "sig", 1100.0, 100.0) is True
    assert health.should_alert(last, "other", 1001.0, 100.0) is True


@pytest.mark.parametrize("raw,expected", [
    (None, 24.0), ("", 24.0), ("6", 6.0), ("0.5", 0.5), ("abc", 24.0), ("nan", 24.0),
    ("inf", 24.0), ("0", 0), ("-3", 0),
])
def test_interval_hours(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("HEALTH_CHECK_INTERVAL_HOURS", raising=False)
    else:
        monkeypatch.setenv("HEALTH_CHECK_INTERVAL_HOURS", raw)
    assert health.interval_hours() == expected


# --- run_health_check / report_failure ---

class Recorder:
    def __init__(self, tg_raises=False, email_raises=False):
        self.tg, self.emails = [], []
        self.tg_raises, self.email_raises = tg_raises, email_raises

    async def send_tg(self, text):
        self.tg.append(text)
        if self.tg_raises:
            raise RuntimeError("telegram down")

    def send_email(self, detail, lines):
        self.emails.append((detail, list(lines)))
        if self.email_raises:
            raise RuntimeError("resend down")


def all_ok(monkeypatch):
    return install(monkeypatch, {
        "api.openai.com": FakeResponse(200, {}),
        "api.anthropic.com": FakeResponse(200, {}),
        "api.perplexity.ai": FakeResponse(400, {}),
        "api.resend.com": FakeResponse(200, {}),
    })


def openai_rejected(monkeypatch, resend_rejected=False):
    return install(monkeypatch, {
        "api.openai.com": FakeResponse(401, {"error": {"code": "invalid_api_key"}}),
        "api.anthropic.com": FakeResponse(200, {}),
        "api.perplexity.ai": FakeResponse(400, {}),
        "api.resend.com": (FakeResponse(401, {"name": "invalid_api_key"}) if resend_rejected
                           else FakeResponse(200, {})),
    })


def run_check(rec, **kwargs):
    kwargs.setdefault("language", "en")
    kwargs.setdefault("now", 1000.0)
    return asyncio.run(health.run_health_check(rec.send_tg, rec.send_email, **kwargs))


def test_all_ok_sends_nothing(monkeypatch):
    all_ok(monkeypatch)
    rec = Recorder()
    statuses = run_check(rec)
    assert [s.state for s in statuses] == ["ok"] * 4
    assert rec.tg == [] and rec.emails == []


def test_one_rejected_alerts_both_channels_once(monkeypatch):
    openai_rejected(monkeypatch)
    rec = Recorder()
    run_check(rec)
    assert len(rec.tg) == 1 and len(rec.emails) == 1
    assert "OpenAI" in rec.tg[0] and "OPENAI_API_KEY" in rec.tg[0]
    detail, lines = rec.emails[0]
    assert "OPENAI_API_KEY" in detail
    assert lines == [rec.tg[0]]


def test_missing_key_counts_as_a_problem(monkeypatch):
    openai_rejected(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    rec = Recorder()
    run_check(rec)
    assert len(rec.tg) == 1 and "not set" in rec.tg[0]


def test_hebrew_alert_text(monkeypatch):
    openai_rejected(monkeypatch)
    rec = Recorder()
    run_check(rec, language="he")
    assert "בדיקה עצמית של הבוט" in rec.tg[0] and "OPENAI_API_KEY" in rec.tg[0]


def test_resend_rejected_alerts_telegram_but_not_email(monkeypatch):
    openai_rejected(monkeypatch, resend_rejected=True)
    rec = Recorder()
    run_check(rec)
    assert len(rec.tg) == 1
    assert "Resend (RESEND_API_KEY)" in rec.tg[0] and "OpenAI (OPENAI_API_KEY)" in rec.tg[0]
    assert rec.emails == []


def test_second_call_inside_the_gap_does_not_alert_again(monkeypatch):
    openai_rejected(monkeypatch)
    rec = Recorder()
    run_check(rec, now=1000.0)
    run_check(rec, now=1000.0 + 19 * 3600)
    assert len(rec.tg) == 1 and len(rec.emails) == 1
    run_check(rec, now=1000.0 + 20 * 3600)
    assert len(rec.tg) == 2 and len(rec.emails) == 2


def test_failure_reason_uses_a_one_hour_gap(monkeypatch):
    openai_rejected(monkeypatch)
    rec = Recorder()
    run_check(rec, reason="failure", now=1000.0)
    run_check(rec, reason="failure", now=1000.0 + 3599)
    assert len(rec.tg) == 1
    run_check(rec, reason="failure", now=1000.0 + 3600)
    assert len(rec.tg) == 2


def test_senders_that_raise_do_not_propagate(monkeypatch):
    openai_rejected(monkeypatch)
    rec = Recorder(tg_raises=True, email_raises=True)
    statuses = run_check(rec)
    assert len(rec.tg) == 1 and len(rec.emails) == 1  # the email is still tried
    assert any(s.state == "rejected" for s in statuses)


def run_failure(rec, error, **kwargs):
    kwargs.setdefault("language", "en")
    kwargs.setdefault("now", 1000.0)
    asyncio.run(health.report_failure(rec.send_tg, rec.send_email, error=error, **kwargs))


def test_report_failure_with_bad_key_behaves_like_health_alert(monkeypatch):
    openai_rejected(monkeypatch)
    rec = Recorder()
    run_failure(rec, "RuntimeError: boom")
    assert len(rec.tg) == 1 and "OPENAI_API_KEY" in rec.tg[0]
    assert len(rec.emails) == 1 and "OPENAI_API_KEY" in rec.emails[0][0]


def test_report_failure_all_keys_ok_sends_email_only_with_type(monkeypatch):
    all_ok(monkeypatch)
    rec = Recorder()
    run_failure(rec, f"AuthenticationError: secret detail {SENTINEL}")
    assert rec.tg == []
    assert len(rec.emails) == 1
    detail, lines = rec.emails[0]
    blob = detail + " " + " ".join(lines)
    assert "AuthenticationError" in blob
    assert "secret detail" not in blob and SENTINEL not in blob


@pytest.mark.parametrize("error,expected", [
    ("", "unknown"),
    ("ValueError", "ValueError"),
    ("yt_dlp.utils.DownloadError: x", "yt_dlp.utils.DownloadError"),
    ("Weird<b>Type: x", "WeirdbType"),
])
def test_report_failure_error_type_is_sanitized(monkeypatch, error, expected):
    all_ok(monkeypatch)
    rec = Recorder()
    run_failure(rec, error)
    assert expected in rec.emails[0][0]
    assert len(rec.emails[0][0]) <= len("processing failed: ") + 60


def test_report_failure_second_call_within_the_hour_sends_no_second_email(monkeypatch):
    all_ok(monkeypatch)
    rec = Recorder()
    run_failure(rec, "ValueError: a", now=1000.0)
    run_failure(rec, "ValueError: b", now=1000.0 + 1800)
    assert len(rec.emails) == 1
    run_failure(rec, "ValueError: c", now=1000.0 + 3600)
    assert len(rec.emails) == 2


def test_report_failure_never_raises(monkeypatch):
    all_ok(monkeypatch)
    rec = Recorder(email_raises=True)
    run_failure(rec, "ValueError: a")
    assert len(rec.emails) == 1

    def boom():
        raise RuntimeError("check_all exploded")

    monkeypatch.setattr(health, "check_all", boom)
    run_failure(rec, "ValueError: a", now=99999.0)


# --- a provider that echoes the key back in its error fields ---

@pytest.mark.parametrize(
    "body",
    [
        {"error": {"type": "sk-SENTINEL-123"}},
        {"error": {"code": "prefix_sk-SENTINEL-123_suffix"}},
        {"name": "skSENTINEL123"},
    ],
)
def test_error_field_that_echoes_the_key_is_dropped(monkeypatch, body):
    install(monkeypatch, {"api.openai.com": FakeResponse(401, body)})
    status = check_one("OPENAI_API_KEY")
    assert status.state == "rejected"
    assert status.detail == "HTTP 401"
    assert "SENTINEL" not in status.detail


def test_ordinary_error_type_is_kept(monkeypatch):
    install(monkeypatch, {"api.openai.com": FakeResponse(401, {"error": {"type": "invalid_request_error"}})})
    assert check_one("OPENAI_API_KEY").detail == "HTTP 401 invalid_request_error"
