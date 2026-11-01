"""What gets queued, for whom, and when (PLAN.md §7.2, §9 "Alerts") — database only, no network."""

import datetime

import pytest
from django.utils import timezone

from apps.accounts import roles
from apps.alerts import outbox
from apps.alerts.models import AlertSettings, Notification
from apps.monitoring.checkers import Result
from apps.monitoring.models import Incident, MaintenanceWindow, Monitor
from apps.monitoring.state import apply_result, pause
from tests.factories import make_user

from .conftest import link

pytestmark = pytest.mark.django_db

OK = Result(ok=True, response_ms=3)
FAIL = Result(ok=False, error_class="timeout", message="No ping reply from 10.0.0.1")


@pytest.fixture
def team(bot, admin, engineer, viewer):
    """Admin and Engineer linked; a Viewer, a muted Engineer and an inactive Admin who must get nothing."""
    link(admin, 1001)
    link(engineer, 1002)
    link(viewer, 1003)
    muted = link(make_user(roles.ENGINEER, username="muted"), 1004)
    muted.profile.alerts_muted = True
    muted.profile.save()
    gone = link(make_user(roles.ADMIN, username="gone"), 1005)
    gone.is_active = False
    gone.save()
    return admin, engineer


def monitor(name="Core firewall", **kw):
    kw.setdefault("type", "ping")
    kw.setdefault("target", "10.0.0.1")
    return Monitor.objects.create(name=name, **kw)


def run(m, result, times=1, **kw):
    for _ in range(times):
        m = apply_result(m, result, **kw)
    return Monitor.objects.get(pk=m.pk)


def queued(event=None, status=None):
    qs = Notification.objects.all()
    if event:
        qs = qs.filter(event=event)
    if status:
        qs = qs.filter(status=status)
    return qs


def deliver_all():
    for n, _chat, _text in outbox.due():
        outbox.mark_sent(n)


class TestDown:
    def test_down_alert_to_admins_and_engineers_once(self, team):
        admin, engineer = team
        m = run(monitor(), FAIL, 3)
        assert outbox.sweep() == 2
        assert set(queued("down").values_list("user", flat=True)) == {admin.pk, engineer.pk}
        text = queued("down").first().text
        assert "🔴 <b>DOWN: Core firewall</b> (ping 10.0.0.1)" in text and "No ping reply" in text
        assert outbox.sweep() == 0  # idempotent
        assert m.status == "down"

    def test_nothing_while_pending(self, team):
        run(monitor(), FAIL, 2)
        assert outbox.sweep() == 0

    def test_nothing_when_switched_off_or_no_bot(self, team):
        run(monitor(), FAIL, 3)
        AlertSettings.objects.filter(pk=1).update(enabled=False)
        assert outbox.sweep() == 0
        AlertSettings.objects.filter(pk=1).update(enabled=True, bot_token_encrypted="")
        assert outbox.sweep() == 0

    def test_html_in_names_is_escaped(self, team):
        run(monitor("R&D <lab>"), FAIL, 3)
        outbox.sweep()
        assert "R&amp;D &lt;lab&gt;" in queued("down").first().text


class TestRecovery:
    def test_up_after_a_delivered_down(self, team):
        m = run(monitor(), FAIL, 3)
        outbox.sweep()
        deliver_all()
        run(m, OK)
        assert outbox.sweep() == 2
        assert queued("recovered").first().text.startswith("🟢 <b>UP: Core firewall</b> — was down")
        assert outbox.sweep() == 0

    def test_up_waits_for_the_down_to_be_delivered(self, team):
        """Internet down: DOWN still queued when the monitor recovers → UP follows once DOWN is out."""
        m = run(monitor(), FAIL, 3)
        outbox.sweep()
        run(m, OK)
        assert outbox.sweep() == 0
        deliver_all()
        assert outbox.sweep() == 2

    def test_only_people_who_got_the_down_get_the_up(self, team, admin, engineer):
        m = run(monitor(), FAIL, 3)
        outbox.sweep()
        deliver_all()
        engineer.profile.alerts_muted = True
        engineer.profile.save()
        run(m, OK)
        outbox.sweep()
        assert list(queued("recovered").values_list("user", flat=True)) == [admin.pk]

    def test_pause_says_paused_not_up(self, team):
        m = run(monitor(), FAIL, 3)
        outbox.sweep()
        deliver_all()
        pause(m)
        outbox.sweep()
        assert queued("recovered").first().text.startswith("⏸ <b>PAUSED: Core firewall</b>")


class TestDependencies:
    def test_unreachable_children_raise_nothing(self, team):
        parent = run(monitor("Switch"), FAIL, 3)
        run(monitor("Camera", target="10.0.0.9", parent=parent), FAIL, 3)
        outbox.sweep()
        assert list(queued("down").values_list("monitor__name", flat=True).distinct()) == ["Switch"]

    def test_child_alert_cancelled_when_parent_goes_down(self, team):
        parent = monitor("Switch")
        run(monitor("Camera", target="10.0.0.9", parent=parent), FAIL, 3)
        outbox.sweep()  # camera's DOWN queued, not yet sent (e.g. Telegram slow)
        run(parent, FAIL, 3)
        outbox.sweep()
        camera = queued("down").filter(monitor__name="Camera")
        assert camera.count() == 2 and all(n.status == "cancelled" for n in camera)
        assert queued("down", "pending").filter(monitor__name="Switch").count() == 2

    def test_affected_count_in_reminders(self, team, bot):
        parent = run(monitor("Switch"), FAIL, 3)
        for i in range(3):
            run(monitor(f"Camera {i}", target=f"10.0.0.{10 + i}", parent=parent), FAIL, 3)
        Incident.objects.filter(monitor=parent).update(started_at=timezone.now() - datetime.timedelta(minutes=61))
        outbox.sweep()
        assert "Affected: 3 monitors unreachable behind it" in queued("reminder").first().text


class TestMaintenance:
    def window(self, m):
        now = timezone.now()
        w = MaintenanceWindow.objects.create(name="Upgrade", starts_at=now - datetime.timedelta(minutes=5),
                                             ends_at=now + datetime.timedelta(hours=1))
        w.monitors.add(m)
        return w

    def test_no_alert_during_maintenance(self, team):
        m = monitor()
        self.window(m)
        run(m, FAIL, 5, in_maintenance=True)
        assert outbox.sweep() == 0

    def test_up_held_back_until_the_window_ends(self, team):
        m = run(monitor(), FAIL, 3)
        outbox.sweep()
        deliver_all()
        m = run(m, OK, in_maintenance=True)  # fixed during the window
        assert m.status == "maintenance" and outbox.sweep() == 0
        run(m, OK)  # window over
        assert outbox.sweep() == 2


class TestReminders:
    def down_since(self, minutes):
        m = run(monitor(), FAIL, 3)
        outbox.sweep()
        deliver_all()
        Incident.objects.filter(monitor=m).update(started_at=timezone.now() - datetime.timedelta(minutes=minutes))
        return m

    def test_hourly_reminder(self, team):
        self.down_since(61)
        assert outbox.sweep() == 2
        text = queued("reminder").first().text
        assert text.startswith("🔴 <b>STILL DOWN: Core firewall</b> — for 1h 01m")
        assert outbox.sweep() == 0  # one per period

    def test_next_period_sends_the_next_reminder(self, team):
        self.down_since(61)
        outbox.sweep()
        Incident.objects.update(started_at=timezone.now() - datetime.timedelta(minutes=125))
        outbox.sweep()
        assert sorted(set(queued("reminder").values_list("sequence", flat=True))) == [1, 2]

    def test_acknowledged_incidents_stop_reminding(self, team, admin):
        self.down_since(61)
        Incident.objects.update(acknowledged_at=timezone.now(), acknowledged_by=admin)
        assert outbox.sweep() == 0

    def test_reminders_can_be_switched_off(self, team, bot):
        self.down_since(61)
        AlertSettings.objects.filter(pk=1).update(reminder_minutes=0)
        assert outbox.sweep() == 0

    def test_pending_reminder_cancelled_after_recovery(self, team):
        m = self.down_since(61)
        outbox.sweep()
        run(m, OK)
        outbox.sweep()
        assert not queued("reminder", "pending").exists() and queued("reminder", "cancelled").count() == 2


class TestSSL:
    def ssl_monitor(self, days):
        return monitor("Mail cert", type="ssl", target="mail.example", port=443, status="up",
                       cert_expires_at=timezone.now() + datetime.timedelta(days=days, hours=1))

    def at(self, hour):
        return timezone.localtime().replace(hour=hour, minute=5)

    def test_daily_warning_inside_the_warning_days(self, team):
        self.ssl_monitor(5)
        self.ssl_monitor(40)
        assert outbox.sweep(now=self.at(9)) == 2
        assert "expires in 5 days" in queued("ssl_expiring").first().text
        assert outbox.sweep(now=self.at(15)) == 0  # once a day
        assert outbox.sweep(now=self.at(9) + datetime.timedelta(days=1)) == 2

    def test_not_before_eight_in_the_morning(self, team):
        self.ssl_monitor(5)
        assert outbox.sweep(now=self.at(6)) == 0

    def test_can_be_switched_off(self, team):
        self.ssl_monitor(5)
        AlertSettings.objects.filter(pk=1).update(ssl_warnings=False)
        assert outbox.sweep(now=self.at(9)) == 0


class TestDelivery:
    def one(self, team, **kw):
        admin, _ = team
        outbox.queue([admin], "down", "hello", subject="x", **kw)
        return Notification.objects.get()

    def test_due_returns_chat_and_text(self, team):
        n = self.one(team)
        assert outbox.due() == [(n, "1001", "hello")]

    def test_late_alerts_are_marked_delayed(self, team):
        self.one(team, now=timezone.now() - datetime.timedelta(minutes=30))
        (_n, _chat, text), = outbox.due()
        assert text.startswith("⏳ <i>Delayed — originally at") and text.endswith("hello")

    def test_backoff_then_give_up_after_a_day(self, team):
        from apps.alerts.telegram import TelegramError

        n = self.one(team)
        outbox.mark_failed(n, TelegramError("Can't reach Telegram", network=True))
        n.refresh_from_db()
        assert n.status == "pending" and n.attempts == 1 and n.next_attempt_at > timezone.now()
        assert outbox.due() == []  # waiting for the retry
        later = timezone.now() + datetime.timedelta(hours=25)
        assert outbox.due(now=later) == []
        n.refresh_from_db()
        assert n.status == "failed"

    def test_permanent_errors_fail_at_once(self, team):
        from apps.alerts.telegram import TelegramError

        n = self.one(team)
        outbox.mark_failed(n, TelegramError("Forbidden: bot was blocked by the user", permanent=True))
        n.refresh_from_db()
        assert n.status == "failed" and "blocked" in n.error

    def test_cancelled_if_the_user_muted_meanwhile(self, team):
        n = self.one(team)
        n.user.profile.alerts_muted = True
        n.user.profile.save()
        assert outbox.due() == []
        n.refresh_from_db()
        assert n.status == "cancelled"


def test_notify_admins_only(team, admin):
    assert outbox.notify_admins("🔒 Vault locked for eng1", subject="vault-eng1") == 1
    assert Notification.objects.get().user == admin
