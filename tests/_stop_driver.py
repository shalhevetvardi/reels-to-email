"""Offline driver for tests/test_graceful_stop.py (underscore name: pytest does not collect it).

Runs the REAL lifecycle - main._build_application and main._run - with a fake network layer
that answers like Telegram, so SIGTERM can be sent to a bot that is in the middle of a link.
Nothing here reaches Telegram, Instagram, a model API or an email provider.

Environment: STOP_DRIVER_LOG = path of the JSON-lines file that records every API call.
STOP_DRIVER_SLOW_START=1 makes the start-up take two seconds, to signal the bot while it starts.
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

CHAT = 4242
LINK = "https://www.instagram.com/reel/AbC123xyz/"
MESSAGE_ID = 10
LOG_PATH = os.environ["STOP_DRIVER_LOG"]

os.environ.update(
    TELEGRAM_BOT_TOKEN="123456:TEST",
    ALLOWED_CHAT_IDS=str(CHAT),
    HEALTH_CHECK_INTERVAL_HOURS="0",
    INTERFACE_LANGUAGE="he",
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# main.py loads a local .env with override=True; a developer's real values must not win here.
import dotenv  # noqa: E402

dotenv.load_dotenv = lambda *args, **kwargs: False

import main  # noqa: E402
from telegram.request import BaseRequest  # noqa: E402


def _record(**fields) -> None:
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(fields, ensure_ascii=False) + "\n")


class FakeRequest(BaseRequest):
    def __init__(self) -> None:
        self._served_link = False

    @property
    def read_timeout(self):
        return 5.0

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def do_request(
        self, url, method, request_data=None, read_timeout=None, write_timeout=None,
        connect_timeout=None, pool_timeout=None,
    ):
        name = url.rsplit("/", 1)[-1]
        params = request_data.parameters if request_data else {}
        result = True

        if name == "getMe":
            result = {"id": 123456, "is_bot": True, "first_name": "Test", "username": "test_bot"}
        elif name == "getUpdates":
            _record(method=name)
            if not self._served_link:
                self._served_link = True
                result = [{
                    "update_id": 1,
                    "message": {
                        "message_id": MESSAGE_ID,
                        "date": int(time.time()),
                        "chat": {"id": CHAT, "type": "private"},
                        "from": {"id": CHAT, "is_bot": False, "first_name": "Sender"},
                        "text": LINK,
                    },
                }]
            else:
                await asyncio.sleep(0.2)
                result = []
        elif name == "sendMessage":
            reply = request_data.json_parameters.get("reply_parameters")
            _record(
                method=name,
                text=params.get("text"),
                reply_to=json.loads(reply)["message_id"] if reply else None,
            )
            result = {
                "message_id": 100,
                "date": int(time.time()),
                "chat": {"id": params["chat_id"], "type": "private"},
                "text": params.get("text", ""),
            }
        else:
            _record(method=name)
            if name == "deleteWebhook" and os.environ.get("STOP_DRIVER_SLOW_START"):
                # Polling is being set up: post_init has run, the application is not running yet.
                print("STARTUP IN PROGRESS", flush=True)
                await asyncio.sleep(2)

        return 200, json.dumps({"ok": True, "result": result}).encode("utf-8")


async def fake_pipeline(url, **kwargs):
    print("PIPELINE STARTED", flush=True)
    await asyncio.sleep(3)
    print("PIPELINE FINISHED", flush=True)
    return {"success": True}


main.run_pipeline = fake_pipeline

application = main._build_application(request=FakeRequest(), get_updates_request=FakeRequest())
main._run(application)
print("DRIVER EXITED CLEANLY", flush=True)
