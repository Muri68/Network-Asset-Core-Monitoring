"""
A minimal Telegram Bot API client on httpx (PLAN.md §4: no heavy SDK). Sync calls are for the web app
(checking a token, test alerts); async calls are for the worker in `run_monitor`.

Only outbound HTTPS to api.telegram.org is needed; updates are fetched by long-polling.
"""

import httpx

API = "https://api.telegram.org"
TRANSPORT = None  # tests swap in an httpx.MockTransport


class TelegramError(Exception):
    """`permanent` errors (chat not found, bot blocked) won't succeed on a retry; `network` ones might."""

    def __init__(self, message, *, code=None, retry_after=None, permanent=False, network=False):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after
        self.permanent = permanent
        self.network = network


def _url(token, method):
    return f"{API}/bot{token}/{method}"


def _parse(response):
    try:
        data = response.json()
    except ValueError:
        raise TelegramError(f"Unexpected reply from Telegram (HTTP {response.status_code})") from None
    if data.get("ok"):
        return data["result"]
    code = data.get("error_code", response.status_code)
    description = data.get("description") or f"HTTP {code}"
    if code in (401, 404):
        description = "Telegram rejected the bot token (Unauthorized)"
    retry_after = (data.get("parameters") or {}).get("retry_after")
    raise TelegramError(description, code=code, retry_after=retry_after, permanent=code in (400, 403))


def _network_error(exc, token):
    reason = str(exc).replace(token, "…") or exc.__class__.__name__
    return TelegramError(f"Can't reach Telegram: {reason}", network=True)


def call(token, method, params=None, *, http_timeout=10):
    try:
        with httpx.Client(timeout=http_timeout, transport=TRANSPORT) as client:
            return _parse(client.post(_url(token, method), json=params or {}))
    except httpx.HTTPError as e:
        raise _network_error(e, token) from e


async def acall(token, method, params=None, *, http_timeout=10):
    try:
        async with httpx.AsyncClient(timeout=http_timeout, transport=TRANSPORT) as client:
            return _parse(await client.post(_url(token, method), json=params or {}))
    except httpx.HTTPError as e:
        raise _network_error(e, token) from e


def message_params(chat_id, text):
    return {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}}


def send(token, chat_id, text):
    return call(token, "sendMessage", message_params(chat_id, text))


async def asend(token, chat_id, text):
    return await acall(token, "sendMessage", message_params(chat_id, text))


def get_me(token):
    return call(token, "getMe")


async def get_updates(token, offset, wait=20):
    """Long-poll: Telegram holds the request up to `wait` seconds until a message arrives."""
    params = {"offset": offset, "timeout": wait, "allowed_updates": ["message"]}
    return await acall(token, "getUpdates", params, http_timeout=wait + 10)
