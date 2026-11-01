"""State machine (PLAN.md §7.1 step 4, §9 "Monitoring") — no network needed."""

import datetime

import pytest
from django.utils import timezone

from apps.monitoring.checkers import Result
from apps.monitoring.models import CheckResult, Incident, MaintenanceWindow, Monitor, monitors_in_maintenance
from apps.monitoring.state import apply_result, pause, resume, status_changed
from tests.inventory.helpers import make_device

# Not transaction=True: those tests empty tables with DELETE, which the append-only audit trigger refuses.
pytestmark = pytest.mark.django_db

OK = Result(ok=True, response_ms=3)
TIMEOUT = Result(ok=False, error_class="timeout", message="No ping reply from 10.0.0.1")
HTTP_503 = Result(ok=False, status_code=503, error_class="http_status", message="HTTP 503 Service Unavailable")


def make_monitor(name="Core firewall", **kw):
    kw.setdefault("type", "ping")
    kw.setdefault("target", "10.0.0.1")
    return Monitor.objects.create(name=name, **kw)


def run(monitor, result, times=1, **kw):
    for _ in range(times):
        monitor = apply_result(monitor, result, **kw)
    return Monitor.objects.get(pk=monitor.pk)


@pytest.fixture
def events(monkeypatch):
    """Status changes announced by the state machine (sent on commit; run straight away in tests)."""
    from django.db import transaction

    monkeypatch.setattr(transaction, "on_commit", lambda func, *args, **kwargs: func())
    seen = []

    def receiver(sender, monitor, old, new, incident, **kwargs):
        seen.append((monitor.name, old, new))

    status_changed.connect(receiver, dispatch_uid="test-events")
    yield seen
    status_changed.disconnect(dispatch_uid="test-events")


class TestRetries:
    def test_first_success_is_up(self, events):
        m = run(make_monitor(), OK)
        assert m.status == "up" and m.last_response_ms == 3 and CheckResult.objects.count() == 1
        assert events == [("Core firewall", "unknown", "up")]

    def test_down_only_after_three_failures(self, events):
        m = run(make_monitor(), OK)
        m = run(m, TIMEOUT, 2)
        assert m.status == "pending" and m.consecutive_failures == 2 and not Incident.objects.exists()
        m = run(m, TIMEOUT)
        assert m.status == "down"
        incident = Incident.objects.get()
        assert incident.is_open and incident.kind == "down" and "3 failed checks in a row" in incident.cause
        assert events[-1] == ("Core firewall", "pending", "down")

    def test_a_success_resets_the_count(self):
        m = run(make_monitor(), TIMEOUT, 2)
        m = run(m, OK)
        m = run(m, TIMEOUT, 2)
        assert m.status == "pending" and not Incident.objects.exists()

    def test_custom_retries(self):
        m = run(make_monitor(retries=1), TIMEOUT)
        assert m.status == "down"

    def test_recovery_closes_the_incident(self, events):
        m = run(make_monitor(), TIMEOUT, 3)
        m = run(m, TIMEOUT, 5)  # stays down, still one incident
        assert Incident.objects.count() == 1
        m = run(m, OK)
        incident = Incident.objects.get()
        assert m.status == "up" and not incident.is_open and incident.ended_at >= incident.started_at
        assert events[-1] == ("Core firewall", "down", "up")

    def test_cause_uses_the_failure_message(self):
        m = run(make_monitor(type="http", target="https://hr.example"), HTTP_503, 3)
        assert Incident.objects.get().cause == "HTTP 503 Service Unavailable" and m.last_error.startswith("HTTP 503")


class TestDependencies:
    def test_child_behind_down_parent_is_unreachable(self, events):
        parent = run(make_monitor("CCTV switch"), TIMEOUT, 3)
        child = make_monitor("NVR 01", target="10.20.0.10", parent=parent)
        child = run(child, TIMEOUT, 3)
        assert child.status == "unreachable"
        incident = child.incidents.get()
        assert incident.kind == "unreachable" and incident.suppressed_by == parent.incidents.get()
        assert "CCTV switch is down" in incident.cause

    def test_parent_via_device_dependency(self):
        switch = make_device("SW-CCTV-01", rack=None)
        nvr = make_device("NVR-01", rack=None, parent=switch)
        parent = run(make_monitor("CCTV switch", device=switch), TIMEOUT, 3)
        child = run(make_monitor("NVR 01", device=nvr, target="10.20.0.10"), TIMEOUT, 3)
        assert child.effective_parent() == parent and child.status == "unreachable"

    def test_grandchildren_point_at_the_root_incident(self):
        core = run(make_monitor("Core"), TIMEOUT, 3)
        sw = run(make_monitor("Switch", parent=core), TIMEOUT, 3)
        cam = run(make_monitor("Camera", parent=sw), TIMEOUT, 3)
        assert cam.status == "unreachable"
        assert cam.incidents.get().suppressed_by == core.incidents.get()

    def test_child_waits_while_parent_is_pending(self):
        """The race in PLAN.md §9: the child reaches 3 failures before the parent does."""
        parent = run(make_monitor("Switch"), TIMEOUT, 2)  # pending
        child = make_monitor("Camera", parent=parent)
        child = run(child, TIMEOUT, 3)
        assert child.status == "pending" and not child.incidents.exists()
        parent = run(parent, TIMEOUT)  # now down
        child = run(child, TIMEOUT)
        assert child.status == "unreachable"

    def test_down_child_becomes_unreachable_when_parent_goes_down(self, events):
        parent = make_monitor("Switch")
        child = run(make_monitor("Camera", parent=parent), TIMEOUT, 3)
        assert child.status == "down"
        parent = run(parent, TIMEOUT, 3)
        child.refresh_from_db()
        incident = child.incidents.get()
        assert child.status == "unreachable" and incident.kind == "unreachable"
        assert incident.suppressed_by == parent.incidents.get()
        assert ("Camera", "down", "unreachable") in events

    def test_child_still_failing_after_parent_recovers_is_down(self):
        parent = run(make_monitor("Switch"), TIMEOUT, 3)
        child = run(make_monitor("Camera", parent=parent), TIMEOUT, 3)
        parent = run(parent, OK)
        child.refresh_from_db()
        assert child.next_check_at <= timezone.now()  # re-checked straight away
        child = run(child, TIMEOUT)
        assert child.status == "down"
        unreachable, down = child.incidents.order_by("started_at")
        assert unreachable.kind == "unreachable" and not unreachable.is_open
        assert down.kind == "down" and down.is_open and "after its parent recovered" in down.cause

    def test_children_recover_with_parent(self):
        parent = run(make_monitor("Switch"), TIMEOUT, 3)
        child = run(make_monitor("Camera", parent=parent), TIMEOUT, 3)
        run(parent, OK)
        child = run(child, OK)
        assert child.status == "up" and not child.incidents.filter(ended_at__isnull=True).exists()


class TestMaintenance:
    def test_failures_during_maintenance_raise_nothing(self, events):
        m = run(make_monitor(), TIMEOUT, 5, in_maintenance=True)
        assert m.status == "maintenance" and not Incident.objects.exists()
        assert m.consecutive_failures == 5  # still counted: the history stays accurate
        assert CheckResult.objects.count() == 5

    def test_state_re_evaluated_when_window_ends(self):
        m = run(make_monitor(), TIMEOUT, 3, in_maintenance=True)
        m = run(m, TIMEOUT)  # window over, still failing
        assert m.status == "down" and Incident.objects.count() == 1

    def test_recovering_inside_window(self):
        m = run(make_monitor(), TIMEOUT, 3)
        m = run(m, OK, in_maintenance=True)
        assert m.status == "maintenance" and not Incident.objects.get().is_open

    def test_window_covers_device_monitors_and_children(self):
        switch = make_device("SW", rack=None)
        cam = make_device("CAM", rack=None, parent=switch)
        m_switch = make_monitor("Switch", device=switch)
        m_cam = make_monitor("Camera", device=cam)
        m_site = make_monitor("Website", type="http", target="https://x.example")
        now = timezone.now()
        w = MaintenanceWindow.objects.create(name="Upgrade", starts_at=now - datetime.timedelta(minutes=5),
                                             ends_at=now + datetime.timedelta(hours=1))
        w.devices.add(switch)
        assert monitors_in_maintenance() == {m_switch.pk, m_cam.pk}
        w.include_children = False
        w.save()
        assert monitors_in_maintenance() == {m_switch.pk}
        assert m_site.pk not in monitors_in_maintenance()


class TestRecurringWindows:
    def window(self, recurrence, start, hours=1):
        return MaintenanceWindow(name="w", starts_at=start, ends_at=start + datetime.timedelta(hours=hours),
                                 recurrence=recurrence)

    def test_one_time(self):
        start = timezone.now() - datetime.timedelta(minutes=10)
        w = self.window("none", start)
        assert w.is_active() and not w.is_active(start + datetime.timedelta(hours=2))

    def test_daily_repeats(self):
        start = timezone.now() - datetime.timedelta(days=3, minutes=10)
        w = self.window("daily", start)
        assert w.is_active()  # three days later, same time of day
        assert not w.is_active(timezone.now() + datetime.timedelta(hours=2))

    def test_weekly_repeats(self):
        start = timezone.now() - datetime.timedelta(days=14, minutes=10)
        w = self.window("weekly", start)
        assert w.is_active() and not w.is_active(timezone.now() + datetime.timedelta(days=1))

    def test_end_now_skips_only_this_occurrence(self):
        start = timezone.now() - datetime.timedelta(days=1, minutes=10)
        w = self.window("daily", start)
        w.ended_early_at = timezone.now()
        assert not w.is_active()
        assert w.is_active(timezone.now() + datetime.timedelta(days=1))  # tomorrow still applies


class TestPause:
    def test_pause_closes_incident_and_resume_rechecks(self, events):
        m = run(make_monitor(), TIMEOUT, 3)
        pause(m)
        m.refresh_from_db()
        assert m.status == "paused" and not m.enabled and not Incident.objects.get().is_open
        resume(m)
        m.refresh_from_db()
        assert m.enabled and m.status == "unknown" and m.next_check_at <= timezone.now()
