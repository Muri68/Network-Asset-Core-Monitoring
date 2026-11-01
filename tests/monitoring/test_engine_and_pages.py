"""The monitor process loop, housekeeping, and the monitoring pages."""

import asyncio
import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.monitoring import engine, stats
from apps.monitoring.checkers import Result
from apps.monitoring.models import (
    CheckResult,
    DailyUptime,
    Incident,
    MaintenanceWindow,
    Monitor,
    MonitorHeartbeat,
)
from apps.monitoring.state import apply_result
from tests.inventory.helpers import make_device

pytestmark = pytest.mark.django_db

TIMEOUT = Result(ok=False, error_class="timeout", message="No ping reply")


def make_monitor(name="Core firewall", **kw):
    kw.setdefault("type", "ping")
    kw.setdefault("target", "10.0.0.1")
    return Monitor.objects.create(name=name, **kw)


@pytest.fixture
def fake_checks(monkeypatch):
    """Replace real network checks: results[monitor name] (default: ok)."""
    results = {}

    async def fake(monitor):
        return results.get(monitor.name, Result(ok=True, response_ms=5))

    monkeypatch.setattr(engine, "run_check", fake)
    return results


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------


class TestEngine:
    def test_claim_due_only_takes_enabled_due_monitors_once(self):
        now = timezone.now()
        due = make_monitor("due", next_check_at=now - datetime.timedelta(seconds=5), interval_s=60)
        make_monitor("later", next_check_at=now + datetime.timedelta(minutes=5))
        make_monitor("paused", enabled=False, next_check_at=now - datetime.timedelta(minutes=5))
        assert [m.name for m in engine.claim_due(now)] == ["due"]
        due.refresh_from_db()
        assert due.next_check_at == now + datetime.timedelta(seconds=60)
        assert engine.claim_due(now) == []  # not picked twice

    def test_batch_applies_results_and_respects_maintenance(self, fake_checks):
        switch = make_device("SW", rack=None)
        m_switch = make_monitor("Switch", device=switch, retries=1)
        m_web = make_monitor("Website", type="http", target="https://x.example", retries=1)
        fake_checks.update({"Switch": TIMEOUT, "Website": TIMEOUT})
        now = timezone.now()
        window = MaintenanceWindow.objects.create(name="w", starts_at=now - datetime.timedelta(minutes=1),
                                                  ends_at=now + datetime.timedelta(hours=1))
        window.devices.add(switch)
        pairs = asyncio.run(engine.run_checks([m_switch, m_web]))  # network part
        assert engine.record_results(pairs) == 2                    # database part
        m_switch.refresh_from_db()
        m_web.refresh_from_db()
        assert m_switch.status == "maintenance" and m_web.status == "down"
        assert list(Incident.objects.values_list("monitor__name", flat=True)) == ["Website"]

    def test_loop_claims_checks_records_and_beats(self, monkeypatch):
        """The loop's wiring, with its database steps replaced by stand-ins."""
        calls = []

        def inline(func):
            async def run(*args, **kwargs):
                return func(*args, **kwargs)
            return run

        monkeypatch.setattr(engine, "sync_to_async", inline)
        monkeypatch.setattr(engine, "housekeeping", lambda: calls.append("housekeeping"))
        monkeypatch.setattr(engine, "claim_due", lambda: calls.append("claim") or ["m1", "m2"])

        async def fake_batch(monitors):
            calls.append(("batch", len(monitors)))
            return len(monitors)

        monkeypatch.setattr(engine, "check_batch", fake_batch)
        monkeypatch.setattr(engine, "beat", lambda started, per_minute: calls.append(("beat", per_minute)))

        async def two_ticks():
            stop = asyncio.Event()
            task = asyncio.create_task(engine.run_forever(stop, tick=0.01))
            while sum(1 for c in calls if c == "claim") < 2:
                await asyncio.sleep(0.005)
            stop.set()
            await task

        asyncio.run(two_ticks())
        assert calls[0] == "housekeeping" and calls.count("housekeeping") == 1  # hourly, not every tick
        assert ("batch", 2) in calls and ("beat", 2) in calls and ("beat", 4) in calls

    def test_heartbeat(self):
        engine.beat(timezone.now(), 12)
        beat = MonitorHeartbeat.current()
        assert beat.alive and beat.checks_last_minute == 12
        MonitorHeartbeat.objects.filter(pk=1).update(last_beat=timezone.now() - datetime.timedelta(minutes=2))
        assert not MonitorHeartbeat.current().alive


class TestHousekeeping:
    def add_results(self, monitor, when, ok_count, fail_count, ms=10):
        for i in range(ok_count + fail_count):
            CheckResult.objects.create(monitor=monitor, checked_at=when + datetime.timedelta(minutes=i),
                                       ok=i < ok_count, response_ms=ms + i if i < ok_count else None)

    def test_rollup_and_retention(self):
        m = make_monitor()
        now = timezone.now()
        yesterday = timezone.localtime(now - datetime.timedelta(days=1)).replace(hour=10, minute=0)
        old = now - datetime.timedelta(days=40)
        self.add_results(m, yesterday, 9, 1)
        self.add_results(m, old, 5, 0)
        deleted = engine.housekeeping(now)
        day = DailyUptime.objects.get(monitor=m, date=yesterday.date())
        assert day.checks == 10 and day.failures == 1 and float(day.uptime_pct) == 90.0 and day.p95_ms == 18
        assert DailyUptime.objects.filter(date=timezone.localdate(old)).exists()  # summarised before deletion
        assert deleted == 5 and CheckResult.objects.count() == 10
        engine.housekeeping(now)  # idempotent
        assert DailyUptime.objects.filter(monitor=m).count() == 2


class TestStats:
    def test_uptime_and_heartbeats(self):
        m = make_monitor()
        for ok in (True, True, True, False):
            CheckResult.objects.create(monitor=m, ok=ok, response_ms=10 if ok else None)
        assert stats.uptime([m.pk], 24) == {m.pk: 75.0}
        assert stats.heartbeats(m, 10).count("down") == 1 and len(stats.heartbeats(m, 2)) == 2
        assert stats.average_ms([m.pk]) == 10
        series = stats.response_series([m.pk], hours=24, buckets=24)
        assert len(series["labels"]) == 6 and series["values"][-1] == 10  # trimmed to where data starts

    def test_status_counts(self):
        make_monitor("a", status="up")
        make_monitor("b", status="down")
        counts = stats.status_counts()
        assert counts["up"] == 1 and counts["down"] == 1 and counts["paused"] == 0


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------


def monitor_post(**kw):
    data = {"name": "Customer portal", "type": "http", "target": "https://portal.example", "interval_s": 60,
            "timeout_s": 10, "retries": 3, "http_method": "GET", "expected_status": "200-299",
            "follow_redirects": "on", "dns_record_type": "A", "ssl_warn_days": 14, "enabled": "on"}
    data.update(kw)
    return data


class TestMonitorPages:
    def test_create_http_monitor(self, as_user, engineer):
        response = as_user(engineer).post(reverse("monitoring:monitor_new"), monitor_post())
        m = Monitor.objects.get()
        assert response.url == m.get_absolute_url() and m.status == "unknown" and m.created_by == engineer
        assert AuditLog.objects.filter(action="create", object_repr="Customer portal").exists()

    @pytest.mark.parametrize("extra", [{"type": "ping"}, {"type": "tcp", "port": 22}])
    def test_create_without_hidden_type_fields(self, as_user, engineer, extra):
        """A ping/TCP form posts no HTTP or DNS settings; the model defaults fill them in."""
        device = make_device("SW-CORE-01", rack=None)
        data = {"name": "Core switch", "target": "10.0.0.1", "interval_s": 60, "timeout_s": 10, "retries": 3,
                "ssl_warn_days": 14, "device": device.pk, "enabled": "on", **extra}
        response = as_user(engineer).post(reverse("monitoring:monitor_new"), data)
        m = Monitor.objects.get()
        assert response.status_code == 302 and m.device == device
        assert (m.http_method, m.expected_status, m.dns_record_type) == ("GET", "200-299", "A")

    def test_form_never_drops_fields_from_the_page(self, as_user, engineer):
        html = as_user(engineer).get(reverse("monitoring:monitor_new")).content.decode()
        assert "x-if" not in html and 'name="http_method"' in html and 'name="dns_record_type"' in html

    @pytest.mark.parametrize("overrides,message", [
        ({"target": "portal.example"}, "full URL"),
        ({"type": "tcp", "target": "10.0.0.1"}, "Enter the port"),
        ({"type": "keyword", "keyword": ""}, "text that must appear"),
        ({"expected_status": "abc"}, "numbers and ranges"),
        ({"interval_s": 10}, "greater than or equal to 20"),
        ({"timeout_s": 60, "interval_s": 30}, "shorter than the check interval"),
    ])
    def test_validation(self, as_user, engineer, overrides, message):
        response = as_user(engineer).post(reverse("monitoring:monitor_new"), monitor_post(**overrides))
        assert response.status_code == 400 and message in response.content.decode()

    def test_monitor_this_device_prefills(self, as_user, engineer):
        device = make_device("FW-CORE-01", rack=None, mgmt_ip="10.10.0.1")
        form = as_user(engineer).get(reverse("monitoring:monitor_new"), {"device": device.pk}).context["form"]
        assert form.initial == {"device": device.pk, "name": "FW-CORE-01", "type": "ping", "target": "10.10.0.1"}

    def test_detail_page(self, as_user, viewer):
        parent = make_monitor("Switch")
        m = make_monitor("Camera", parent=parent)
        for _ in range(3):
            m = apply_result(m, TIMEOUT)
        html = as_user(viewer).get(m.get_absolute_url()).content.decode()
        assert "Switch" in html and "No ping reply" in html and "Incident history" in html
        assert reverse("monitoring:monitor_edit", args=[m.pk]) not in html  # viewer: no edit

    def test_pause_resume_and_check_now(self, as_user, engineer):
        m = make_monitor(next_check_at=timezone.now() + datetime.timedelta(hours=1))
        client = as_user(engineer)
        client.post(reverse("monitoring:monitor_action", args=[m.pk, "check"]))
        m.refresh_from_db()
        assert m.next_check_at <= timezone.now()
        client.post(reverse("monitoring:monitor_action", args=[m.pk, "pause"]))
        m.refresh_from_db()
        assert m.status == "paused" and not m.enabled
        client.post(reverse("monitoring:monitor_action", args=[m.pk, "resume"]))
        m.refresh_from_db()
        assert m.enabled

    def test_list_filters(self, as_user, viewer):
        make_monitor("Down one", status="down")
        make_monitor("Up one", status="up")
        page = as_user(viewer).get(reverse("monitoring:monitors"), {"status": "down"})
        assert [m.name for m in page.context["monitors"]] == ["Down one"]

    def test_device_monitors_tab(self, as_user, viewer):
        device = make_device("FW", rack=None)
        make_monitor("Firewall ping", device=device)
        assert "Firewall ping" in as_user(viewer).get(device.get_absolute_url()).content.decode()


class TestIncidentsAndMaintenance:
    def test_acknowledge(self, as_user, engineer):
        m = make_monitor(retries=1)
        apply_result(m, TIMEOUT)
        incident = Incident.objects.get()
        as_user(engineer).post(reverse("monitoring:incident_ack", args=[incident.pk]))
        incident.refresh_from_db()
        assert incident.acknowledged_by == engineer
        assert AuditLog.objects.filter(action="incident_ack").exists()

    def test_viewer_cannot_acknowledge(self, as_user, viewer):
        m = make_monitor(retries=1)
        apply_result(m, TIMEOUT)
        assert as_user(viewer).post(reverse("monitoring:incident_ack",
                                            args=[Incident.objects.get().pk])).status_code == 403

    def test_schedule_and_end_window(self, as_user, engineer):
        device = make_device("SRV-DB-01", rack=None)
        client = as_user(engineer)
        start = timezone.localtime() - datetime.timedelta(minutes=5)
        response = client.post(reverse("monitoring:maintenance_new"), {
            "name": "DB patching", "starts_at": start.strftime("%Y-%m-%dT%H:%M"),
            "ends_at": (start + datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
            "recurrence": "none", "devices": [device.pk], "include_children": "on", "reason": "OS updates",
        })
        assert response.url == reverse("monitoring:maintenance")
        window = MaintenanceWindow.objects.get()
        assert window.is_active() and window.created_by == engineer
        client.post(reverse("monitoring:maintenance_end", args=[window.pk]))
        window.refresh_from_db()
        assert not window.is_active() and window.state == "completed"

    def test_window_needs_targets_and_valid_times(self, as_user, engineer):
        now = timezone.localtime()
        response = as_user(engineer).post(reverse("monitoring:maintenance_new"), {
            "name": "x", "starts_at": now.strftime("%Y-%m-%dT%H:%M"),
            "ends_at": (now - datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"), "recurrence": "none"})
        html = response.content.decode()
        assert response.status_code == 400 and "at least one device or monitor" in html and "after the start" in html


class TestDashboard:
    def test_checker_banner_when_not_running(self, as_user, viewer):
        html = as_user(viewer).get(reverse("ui:dashboard")).content.decode()
        assert "The checker isn't running" in html and "Checker not running" in html

    def test_shows_real_status_and_incidents(self, as_user, viewer):
        MonitorHeartbeat.objects.create(pk=1, last_beat=timezone.now(), started_at=timezone.now())
        down = make_monitor("HR portal", type="http", target="https://hr.example", retries=1)
        apply_result(down, Result(ok=False, error_class="http_status", message="HTTP 503 Service Unavailable"))
        make_monitor("Website", status="up")
        response = as_user(viewer).get(reverse("ui:dashboard"))
        html = response.content.decode()
        assert response.context["counts"]["down"] == 1 and response.context["counts"]["up"] == 1
        assert "HR portal" in html and "HTTP 503" in html and "Checker running" in html
        assert "The checker isn't running" not in html


def test_response_series_starts_where_data_starts():
    m = make_monitor()
    CheckResult.objects.create(monitor=m, ok=True, response_ms=20)
    series = stats.response_series([m.pk], hours=24, buckets=48)
    assert len(series["values"]) == 6 and series["values"][-1] == 20


def test_uptime_chart_floor_drops_for_bad_days(as_user, viewer):
    m = make_monitor()
    for ok in (True, False, False, False):
        CheckResult.objects.create(monitor=m, ok=ok)
    chart = as_user(viewer).get(reverse("ui:dashboard")).context["chart_uptime"]
    assert chart["values"][-1] == 25.0 and chart["min"] == 24
