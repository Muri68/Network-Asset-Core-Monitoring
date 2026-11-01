"""The worker against a fake Telegram: delivery, outages, linking, /status (PLAN.md §9 "Alerts")."""

import asyncio
import datetime

import pytest
from django.utils import timezone

from apps.alerts import outbox, telegram, worker
from apps.alerts.models import AlertSettings, Notification
from apps.audit.models import AuditLog
from apps.monitoring.models import Incident, Monitor

from .conftest import link

pytestmark = pytest.mark.django_db


def deliver():
    """worker.deliver_round with its database steps in the test's thread (see tests/monitoring notes)."""
    token, batch = worker.prepare()
    return worker.record(asyncio.run(worker.send_batch(token, batch)))


def poll():
    """worker.poll_round, split the same way."""
    token, offset = worker.poll_state()
    if not token:
        return False
    updates = asyncio.run(telegram.get_updates(token, offset, wait=0))
    asyncio.run(worker.send_replies(token, worker.handle_updates(updates)))
    return True


def down_incident(name="Core firewall"):
    m = Monitor.objects.create(name=name, type="ping", target="10.0.0.1", status="down")
    return Incident.objects.create(monitor=m, cause="No ping reply from 10.0.0.1 — 3 failed checks in a row")


class TestDelivery:
    def test_down_alert_reaches_the_phone(self, fake_tg, bot, admin):
        link(admin, 1001)
        down_incident()
        assert deliver() is True
        (text,) = fake_tg.texts(chat_id="1001")
        assert "DOWN: Core firewall" in text and fake_tg.sent[0]["parse_mode"] == "HTML"
        assert Notification.objects.get().status == "sent"

    def test_outage_keeps_alerts_queued_then_sends_them_marked_delayed(self, fake_tg, bot, admin):
        link(admin, 1001)
        down_incident()
        fake_tg.fail = "network"
        assert deliver() is False
        n = Notification.objects.get()
        assert n.status == "pending" and "Can't reach Telegram" in n.error
        fake_tg.fail = None
        Notification.objects.update(next_attempt_at=timezone.now(),
                                    created_at=timezone.now() - datetime.timedelta(minutes=20))
        deliver()
        assert fake_tg.texts()[0].startswith("⏳ <i>Delayed — originally at")
        assert Notification.objects.get().status == "sent"

    def test_blocked_bot_fails_without_retrying(self, fake_tg, bot, admin):
        link(admin, 1001)
        down_incident()
        fake_tg.fail = (403, "Forbidden: bot was blocked by the user")
        deliver()
        assert Notification.objects.get().status == "failed"

    def test_token_never_appears_in_errors(self, fake_tg, bot, admin):
        from tests.alerts.conftest import TOKEN

        link(admin, 1001)
        down_incident()
        fake_tg.fail = (401, "Unauthorized")
        deliver()
        n = Notification.objects.get()
        assert "rejected the bot token" in n.error and TOKEN not in n.error

    def test_rounds_glue_the_steps_together(self, monkeypatch):
        calls = []
        monkeypatch.setattr(worker, "prepare", lambda: calls.append("prepare") or ("tok", []))
        monkeypatch.setattr(worker, "record", lambda results: calls.append(("record", results)) or True)
        assert asyncio.run(worker.deliver_round()) is True and calls == ["prepare", ("record", [])]

    def test_loop_backs_off_while_telegram_is_unreachable_and_stops_cleanly(self, monkeypatch):
        rounds = []

        async def unreachable():
            rounds.append(1)
            return False

        sleeps = []

        async def fake_sleep(stop, seconds):
            sleeps.append(seconds)
            if len(sleeps) == 4:
                stop.set()

        monkeypatch.setattr(worker, "deliver_round", unreachable)
        monkeypatch.setattr(worker, "_sleep", fake_sleep)
        asyncio.run(worker.deliver_loop(asyncio.Event()))
        assert sleeps == [10, 20, 40, 80]


class TestLinking:
    def test_start_with_token_links_the_chat(self, fake_tg, bot, engineer):
        token, _ = outbox.start_link(engineer)
        fake_tg.message(5, 777, f"/start {token}", first_name="Eve")
        assert poll() is True
        engineer.profile.refresh_from_db()
        assert engineer.profile.telegram_chat_id == "777" and engineer.profile.telegram_name == "Eve (@eve)"
        assert engineer.profile.telegram_link_hash == ""  # one-time
        assert "✅ Linked to NetCore as <b>Eve Engineer</b>" in fake_tg.texts("777")[0]
        assert AlertSettings.load().last_update_id == 5
        assert AuditLog.objects.filter(action="telegram_link", actor=engineer).exists()

    def test_updates_are_not_processed_twice(self, fake_tg, bot, engineer):
        fake_tg.message(5, 777, "/help")
        poll()
        poll()
        assert len(fake_tg.sent) == 1

    def test_expired_or_reused_token_is_refused(self, fake_tg, bot, engineer):
        token, _ = outbox.start_link(engineer)
        engineer.profile.telegram_link_expires = timezone.now() - datetime.timedelta(seconds=1)
        engineer.profile.save()
        fake_tg.message(1, 777, f"/start {token}")
        poll()
        engineer.profile.refresh_from_db()
        assert not engineer.profile.telegram_chat_id and "expired" in fake_tg.texts()[0]

    def test_chat_moves_to_the_new_user(self, fake_tg, bot, admin, engineer):
        link(admin, 777)
        token, _ = outbox.start_link(engineer)
        fake_tg.message(1, 777, f"/start {token}")
        poll()
        admin.profile.refresh_from_db()
        engineer.profile.refresh_from_db()
        assert admin.profile.telegram_chat_id is None and engineer.profile.telegram_chat_id == "777"

    def test_group_chats_are_ignored(self, fake_tg, bot):
        fake_tg.updates.append({"update_id": 1, "message": {"text": "/status", "chat": {"id": -5, "type": "group"}}})
        poll()
        assert fake_tg.sent == []

    def test_no_bot_no_polling(self, fake_tg, db):
        assert poll() is False


class TestStatusCommand:
    def test_linked_user_gets_a_summary(self, fake_tg, bot, admin):
        link(admin, 1001)
        down_incident("Core firewall")
        Monitor.objects.create(name="Portal", type="http", target="https://portal.example", status="up")
        fake_tg.message(1, 1001, "/status")
        poll()
        reply = fake_tg.texts("1001")[0]
        assert "🟢 1 up · 🔴 1 down" in reply and "Core firewall" in reply

    def test_strangers_get_nothing_about_the_network(self, fake_tg, bot):
        down_incident("Core firewall")
        fake_tg.message(1, 999, "/status")
        poll()
        reply = fake_tg.texts("999")[0]
        assert "isn't linked" in reply and "Core firewall" not in reply
