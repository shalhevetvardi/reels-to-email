"""
Webhook mode: how the bot is told to listen, and the guard against repeated updates.

On a Render free web service the instance sleeps after 15 minutes without an inbound request and
Telegram's webhook request is what wakes it. Locally, and anywhere else a long-lived process is
fine, the bot keeps long-polling. This module holds the parts of that choice that need no Telegram
objects, so they can be tested without a network.

Fail-closed: a webhook is a public URL, so it is never started without a secret that Telegram
sends in a header on every request and that the server checks.
"""

import hashlib
import hmac
import re
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field

MIN_SECRET_LENGTH = 32
# Telegram's own limit on secret_token, and the only characters it accepts.
_MAX_SECRET_LENGTH = 256
_SECRET_PATTERN = re.compile(r"[A-Za-z0-9_-]+")

_PATH_LABEL = b"reels-to-email/telegram-webhook-path/v1"

SEEN_UPDATES_LIMIT = 1000


class WebhookConfigError(RuntimeError):
    """The environment asks for webhook mode but cannot run it safely.

    Messages name the variable and the rule, never a value: this text goes to the log.
    """


@dataclass(frozen=True)
class WebhookConfig:
    listen: str
    port: int
    # The next three are secrets or derived from one: kept out of repr, and never logged.
    url_path: str = field(repr=False)
    webhook_url: str = field(repr=False)
    secret_token: str = field(repr=False)


def derive_url_path(secret: str) -> str:
    """A path nobody can guess without the secret, and that cannot be turned back into it.

    The path is a second lock next to the secret header: scanners that hit the service find no
    route at all instead of a route that answers 403.
    """
    return hmac.new(secret.encode("utf-8"), _PATH_LABEL, hashlib.sha256).hexdigest()


def _clean(value: str | None) -> str:
    return (value or "").strip()


def load_webhook_config(env: Mapping[str, str]) -> WebhookConfig | None:
    """Return the webhook settings, or None for polling.

    Webhook mode is chosen when TELEGRAM_WEBHOOK_SECRET is set. An explicit
    TELEGRAM_WEBHOOK_BASE_URL without a secret is a half-finished webhook setup, so it is an error
    and not a silent fall back to polling (on a sleeping instance polling would look like a dead
    bot). RENDER_EXTERNAL_URL alone does not select the mode: it only supplies the address.
    """
    secret_set = "TELEGRAM_WEBHOOK_SECRET" in env
    explicit_base = _clean(env.get("TELEGRAM_WEBHOOK_BASE_URL"))

    if not secret_set and not explicit_base:
        return None

    secret = _clean(env.get("TELEGRAM_WEBHOOK_SECRET"))
    if not secret:
        raise WebhookConfigError(
            "TELEGRAM_WEBHOOK_SECRET is missing or empty, but webhook mode was requested. "
            f"Set it to a random string of at least {MIN_SECRET_LENGTH} characters."
        )
    if len(secret) < MIN_SECRET_LENGTH:
        raise WebhookConfigError(
            f"TELEGRAM_WEBHOOK_SECRET is shorter than {MIN_SECRET_LENGTH} characters."
        )
    if len(secret) > _MAX_SECRET_LENGTH or not _SECRET_PATTERN.fullmatch(secret):
        raise WebhookConfigError(
            f"TELEGRAM_WEBHOOK_SECRET must be at most {_MAX_SECRET_LENGTH} characters, "
            "only A-Z a-z 0-9 _ - (a Telegram requirement). Example: openssl rand -hex 32"
        )

    base = explicit_base or _clean(env.get("RENDER_EXTERNAL_URL"))
    if not base:
        raise WebhookConfigError(
            "No public address for the webhook: set TELEGRAM_WEBHOOK_BASE_URL "
            "(Render sets RENDER_EXTERNAL_URL by itself on web services)."
        )
    if not base.lower().startswith("https://"):
        raise WebhookConfigError("The webhook base URL must start with https:// (Telegram's rule).")

    raw_port = _clean(env.get("PORT"))
    try:
        port = int(raw_port)
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        raise WebhookConfigError(
            "PORT is missing or not a valid port number (Render sets it on web services)."
        )

    path = derive_url_path(secret)
    return WebhookConfig(
        listen="0.0.0.0",
        port=port,
        url_path=path,
        webhook_url=f"{base.rstrip('/')}/{path}",
        secret_token=secret,
    )


class _BoundedSet:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._items: OrderedDict = OrderedDict()

    def add(self, item) -> bool:
        """Remember item. Return True if it was new."""
        if item in self._items:
            self._items.move_to_end(item)
            return False
        self._items[item] = None
        if len(self._items) > self._limit:
            self._items.popitem(last=False)
        return True


class SeenUpdates:
    """Lets each Telegram update through once per process lifetime.

    Telegram sends an update again when the first request got no answer in time, which is what a
    cold start of a sleeping instance looks like. Both deliveries then reach the same, now running,
    process, so memory is enough. It is not enough when the process dies between the two
    deliveries (a redeploy after the answer was lost): the new process has not seen the update and
    handles it once more. That window is narrow, and the cost of a repeat is one more email.
    """

    def __init__(self, limit: int = SEEN_UPDATES_LIMIT) -> None:
        self._update_ids = _BoundedSet(limit)
        self._messages = _BoundedSet(limit)

    def first_time(
        self, update_id: int | None, chat_id: int | None = None, message_id: int | None = None
    ) -> bool:
        """Record the update and return True only the first time it is seen.

        The (chat, message) pair is a second key for a redelivery that came with a new update id.
        Pass it for new messages only: an edit keeps the message id, and has its own update id.
        """
        new = True
        if update_id is not None and not self._update_ids.add(update_id):
            new = False
        if chat_id is not None and message_id is not None:
            if not self._messages.add((chat_id, message_id)):
                new = False
        return new
