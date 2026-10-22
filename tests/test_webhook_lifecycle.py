"""The real webhook lifecycle, offline: the bot's own server on the loopback, a fake Telegram API.

Proves what the unit tests cannot: that PTB answers 200 before the link is processed, that the
wrong secret and the wrong path are refused, that a repeated update runs the pipeline once, and
that a stop signal still tells the sender and lets the link finish. Nothing leaves this machine.
"""
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from messages import t  # noqa: E402
from webhook_mode import derive_url_path  # noqa: E402

SECRET = "lifecycle_" + "k" * 40
URL = "https://www.instagram.com/reel/AbC123xyz/"
CHAT = 4242

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(signal, "SIGTERM"),
    reason="asyncio signal handlers are not available on Windows",
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _update(update_id: int, message_id: int = 10) -> bytes:
    return json.dumps({
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "date": int(time.time()),
            "chat": {"id": CHAT, "type": "private"},
            "from": {"id": CHAT, "is_bot": False, "first_name": "Sender"},
            "text": URL,
        },
    }).encode("utf-8")


def _post(port: int, path: str, body: bytes, secret: str | None, content_type="application/json") -> int:
    headers = {"Content-Type": content_type}
    if secret is not None:
        headers["X-Telegram-Bot-Api-Secret-Token"] = secret
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("POST", path, body=body, headers=headers)
        return conn.getresponse().status
    finally:
        conn.close()


def _api_calls(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


def _wait_until_listening(port: int, proc, lines) -> None:
    for _ in range(200):  # at most 20 seconds
        if proc.poll() is not None:
            pytest.fail("bot exited during start-up:\n" + "\n".join(lines))
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return
        except OSError:
            time.sleep(0.1)
    pytest.fail("the webhook server never started listening:\n" + "\n".join(lines))


def test_real_webhook_lifecycle(tmp_path):
    port = _free_port()
    path = "/" + derive_url_path(SECRET)
    log_path = tmp_path / "api-calls.jsonl"
    env = {
        **os.environ,
        "STOP_DRIVER_LOG": str(log_path),
        "STOP_DRIVER_WEBHOOK_PORT": str(port),
        "STOP_DRIVER_WEBHOOK_SECRET": SECRET,
        "PYTHONUNBUFFERED": "1",
    }
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "tests" / "_stop_driver.py")],
        cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    lines: list[str] = []
    started = threading.Event()

    def read():
        for line in proc.stdout:
            lines.append(line.rstrip("\n"))
            if "PIPELINE STARTED" in line:
                started.set()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()

    try:
        _wait_until_listening(port, proc, lines)

        # Refused: no secret, wrong secret, wrong path, wrong content type.
        assert _post(port, path, _update(1), None) == 403
        assert _post(port, path, _update(1), SECRET + "x") == 403
        assert _post(port, path, _update(1), "wrong") == 403
        assert _post(port, "/" + "0" * 64, _update(1), SECRET) == 404
        assert _post(port, path, _update(1), SECRET, content_type="text/plain") == 403
        assert not started.is_set()

        # Accepted: the answer comes while the link is still being processed (the fake pipeline
        # takes three seconds), so Telegram would not time out on a slow link.
        assert _post(port, path, _update(1), SECRET) == 200
        assert not any("PIPELINE FINISHED" in line for line in lines)
        assert started.wait(timeout=20), "pipeline never started:\n" + "\n".join(lines)

        # Telegram sends it again (same update id), and once more under a new update id.
        assert _post(port, path, _update(1), SECRET) == 200
        assert _post(port, path, _update(2), SECRET) == 200

        proc.send_signal(signal.SIGTERM)
        try:
            exit_code = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pytest.fail("bot did not exit after SIGTERM:\n" + "\n".join(lines))
        reader.join(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    output = "\n".join(lines)
    calls = _api_calls(log_path)
    texts = [c["text"] for c in calls if c["method"] == "sendMessage"]

    # Telegram was told the derived address and the secret.
    (registered,) = [c for c in calls if c["method"] == "setWebhook"]
    assert registered["url"] == f"https://bot.example.test{path}"
    assert registered["secret_token"] == SECRET
    assert not any(c["method"] == "getUpdates" for c in calls)

    # Graceful stop works in this mode too: notice first, then the finished link.
    notice = t("update_in_progress", "he", url=URL)
    arrived = t("email_arrived", "he")
    assert exit_code == 0, output
    assert texts.count(notice) == 1 and texts.count(arrived) == 1, output
    assert texts.index(notice) < texts.index(arrived)
    assert "PIPELINE FINISHED" in output

    # The three posts of the link ran the pipeline once.
    assert sum("PIPELINE STARTED" in line for line in lines) == 1, output
    assert texts.count(t("got_it", "he")) == 1, output
    assert sum("Ignored an update Telegram delivered again" in line for line in lines) == 2, output

    # Neither the secret nor the path was logged.
    assert SECRET not in output and path.lstrip("/") not in output
