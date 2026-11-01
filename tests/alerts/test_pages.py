"""Settings → Telegram alerts and Profile → Telegram (PLAN.md §7.2)."""

import pytest
from django.test import override_settings
from django.urls import reverse

from apps.alerts import outbox
from apps.alerts.models import AlertSettings, Notification
from apps.audit.models import AuditLog
from apps.core import crypto

from .conftest import TOKEN, link

pytestmark = pytest.mark.django_db


def save(client, **data):
    data.setdefault("reminder_minutes", 60)
    data.setdefault("enabled", "on")
    return client.post(reverse("alerts:settings_save"), data)


class TestSettings:
    def test_admin_adds_the_bot(self, as_user, admin, fake_tg):
        response = save(as_user(admin), bot_token=TOKEN, ssl_warnings="on")
        assert response.status_code == 302 and response.url.endswith("?tab=alerts")
        s = AlertSettings.load()
        assert s.bot_token == TOKEN and s.bot_username == "netcore_test_bot" and s.active
        assert TOKEN not in s.bot_token_encrypted  # stored encrypted
        entry = AuditLog.objects.get(action="settings_change")
        assert entry.changes["bot_token"] == ["(hidden)", "(replaced)"] and TOKEN not in str(entry.changes)

    def test_page_shows_the_bot_without_the_token(self, as_user, admin, bot):
        html = as_user(admin).get(reverse("ui:settings") + "?tab=alerts").content.decode()
        assert "@netcore_test_bot" in html and "123456789:••••AAAA" in html and TOKEN not in html
        assert "tab: 'alerts'" in html

    def test_token_rejected_by_telegram(self, as_user, admin, fake_tg):
        fake_tg.fail = (401, "Unauthorized")
        response = save(as_user(admin), bot_token=TOKEN)
        assert response.status_code == 400 and "rejected the bot token" in response.content.decode()
        assert not AlertSettings.load().configured

    def test_badly_formatted_token(self, as_user, admin, fake_tg):
        response = save(as_user(admin), bot_token="hello")
        assert response.status_code == 400 and "look like a bot token" in response.content.decode()

    def test_token_required_the_first_time_only(self, as_user, admin, bot):
        client = as_user(admin)
        assert save(client, reminder_minutes=30).status_code == 302  # keeps the token
        assert AlertSettings.load().reminder_minutes == 30 and AlertSettings.load().bot_token == TOKEN
        AlertSettings.objects.update(bot_token_encrypted="", bot_username="")
        assert save(client).status_code == 400

    def test_a_different_bot_unlinks_everyone(self, as_user, admin, engineer, bot, fake_tg):
        link(engineer, 1002)
        fake_tg.username = "other_bot"
        save(as_user(admin), bot_token="987654321:" + "B" * 35)
        engineer.profile.refresh_from_db()
        assert engineer.profile.telegram_chat_id is None and AlertSettings.load().bot_username == "other_bot"

    @pytest.mark.parametrize("who", ["engineer", "viewer"])
    def test_only_admins(self, as_user, who, request, fake_tg):
        user = request.getfixturevalue(who)
        assert save(as_user(user), bot_token=TOKEN).status_code == 403
        assert as_user(user).get(reverse("ui:settings")).status_code == 403

    def test_test_alert(self, as_user, admin, bot, fake_tg):
        link(admin, 1001)
        response = as_user(admin).post(reverse("alerts:settings_test"), follow=True)
        assert "Test alert sent" in response.content.decode()
        assert "Test alert from NetCore" in fake_tg.texts("1001")[0]
        assert Notification.objects.get(event="test").status == "sent"

    def test_test_alert_without_a_linked_chat(self, as_user, admin, bot, fake_tg):
        response = as_user(admin).post(reverse("alerts:settings_test"), follow=True)
        assert "Link your Telegram" in response.content.decode() and fake_tg.sent == []


class TestProfile:
    def card(self, client):
        return client.get(reverse("alerts:card")).content.decode()

    def test_no_bot_yet(self, as_user, engineer):
        assert "aren't set up yet" in self.card(as_user(engineer))

    def test_link_flow(self, as_user, engineer, bot):
        client = as_user(engineer)
        client.post(reverse("alerts:link"))
        html = self.card(client)
        assert "https://t.me/netcore_test_bot?start=" in html and 'hx-trigger="every 3s"' in html
        token = html.split("?start=")[1].split('"')[0]
        # the user presses Start in Telegram; the worker handles the message
        outbox.handle_update({"update_id": 1, "message": {"text": f"/start {token}", "from": {"first_name": "Eve"},
                                                          "chat": {"id": 555, "type": "private"}}})
        html = self.card(client)
        assert "Linked" in html and "Eve" in html and "every 3s" not in html

    def test_viewers_are_not_offered_linking(self, as_user, viewer, bot):
        html = self.card(as_user(viewer))
        assert "Link Telegram" not in html and "Admins and Engineers" in html

    def test_mute_unmute_and_unlink(self, as_user, engineer, bot):
        link(engineer, 1002)
        client = as_user(engineer)
        client.post(reverse("alerts:mute"))
        engineer.profile.refresh_from_db()
        assert engineer.profile.alerts_muted and outbox.recipients() == []
        client.post(reverse("alerts:mute"))
        engineer.profile.refresh_from_db()
        assert not engineer.profile.alerts_muted
        client.post(reverse("alerts:unlink"))
        engineer.profile.refresh_from_db()
        assert engineer.profile.telegram_chat_id is None
        actions = list(AuditLog.objects.filter(actor=engineer).values_list("action", flat=True))
        assert {"alerts_mute", "alerts_unmute", "telegram_unlink"} <= set(actions)

    def test_test_alert_failure_is_explained(self, as_user, engineer, bot, fake_tg):
        link(engineer, 1002)
        fake_tg.fail = (403, "Forbidden: bot was blocked by the user")
        response = as_user(engineer).post(reverse("alerts:test"), follow=True)
        assert "Test alert not sent: Forbidden: bot was blocked by the user" in response.content.decode()

    def test_profile_page_includes_the_card(self, as_user, engineer, bot):
        html = as_user(engineer).get(reverse("accounts:profile")).content.decode()
        assert 'id="telegram"' in html and "Link Telegram" in html


class TestCrypto:
    def test_round_trip(self):
        assert crypto.decrypt(crypto.encrypt("s3cret")) == "s3cret"

    def test_old_keys_still_decrypt_after_adding_a_new_one(self):
        old, new = crypto.new_key(), crypto.new_key()
        with override_settings(ENCRYPTION_KEYS=[old]):
            token = crypto.encrypt("s3cret")
        with override_settings(ENCRYPTION_KEYS=[new, old]):
            assert crypto.decrypt(token) == "s3cret"
        with override_settings(ENCRYPTION_KEYS=[new]):
            assert crypto.decrypt(token) == ""

    def test_production_needs_a_key(self):
        from django.core.exceptions import ImproperlyConfigured

        with override_settings(ENCRYPTION_KEYS=[], DEBUG=False), pytest.raises(ImproperlyConfigured):
            crypto.encrypt("x")
