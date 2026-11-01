import json

import httpx
import pytest

from apps.alerts import telegram
from apps.alerts.models import AlertSettings

TOKEN = "123456789:" + "A" * 35


class FakeTelegram:
    """Stands in for api.telegram.org: records sent messages, serves queued updates."""

    def __init__(self):
        self.sent = []
        self.updates = []
        self.fail = None  # "network", or (http code, description)
        self.username = "netcore_test_bot"

    def __call__(self, request):
        if self.fail == "network":
            raise httpx.ConnectError("[Errno 65] No route to host")
        method = request.url.path.rsplit("/", 1)[1]
        body = json.loads(request.content or b"{}")
        if self.fail:
            code, description = self.fail
            return httpx.Response(code, json={"ok": False, "error_code": code, "description": description})
        if method == "getMe":
            return self.ok({"id": 123456789, "is_bot": True, "username": self.username})
        if method == "sendMessage":
            self.sent.append(body)
            return self.ok({"message_id": len(self.sent)})
        if method == "getUpdates":
            return self.ok([u for u in self.updates if u["update_id"] >= body.get("offset", 0)])
        return httpx.Response(404, json={"ok": False, "error_code": 404, "description": "Not Found"})

    @staticmethod
    def ok(result):
        return httpx.Response(200, json={"ok": True, "result": result})

    def texts(self, chat_id=None):
        return [m["text"] for m in self.sent if chat_id is None or m["chat_id"] == chat_id]

    def message(self, update_id, chat_id, text, first_name="Ada"):
        self.updates.append({"update_id": update_id, "message": {
            "message_id": update_id, "text": text, "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "first_name": first_name, "username": first_name.lower()}}})


@pytest.fixture
def fake_tg(monkeypatch):
    fake = FakeTelegram()
    monkeypatch.setattr(telegram, "TRANSPORT", httpx.MockTransport(fake))
    return fake


@pytest.fixture
def bot(db):
    s = AlertSettings.load()
    s.bot_token = TOKEN
    s.bot_username = "netcore_test_bot"
    s.save()
    return s


def link(user, chat_id):
    profile = user.profile
    profile.telegram_chat_id = str(chat_id)
    profile.save()
    return user
