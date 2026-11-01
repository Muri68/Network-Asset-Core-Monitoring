"""
UI prototype views. Every view renders fake data from fake_data.py.

Nothing here is persisted; forms are marked data-prototype and only show a toast.
These views are replaced app-by-app as the phases in PLAN.md land.
"""

from collections import Counter
from datetime import timedelta

from django.shortcuts import render
from django.utils import timezone

from apps.accounts.decorators import capability_required
from apps.accounts.roles import can
from apps.audit.models import AuditLog
from apps.inventory.models import Device
from apps.ipam.models import Prefix
from apps.monitoring import stats as monitor_stats
from apps.monitoring.models import Incident, MaintenanceWindow, Monitor, human_duration

from . import fake_data as fd


def _page(request, template, active, **ctx):
    ctx["active"] = active
    return render(request, template, ctx)


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------


def _dashboard_counters():
    return {"counts": monitor_stats.status_counts(), "total": Monitor.objects.count(), "updated": timezone.now()}


def dashboard(request):
    today = timezone.localdate()
    live = Device.objects.exclude(status=Device.Status.RETIRED)
    type_counts = Counter(live.values_list("device_type", flat=True))
    donut_keys = ["switch", "server", "camera", "access_point", "other"]
    donut_vals = [type_counts["switch"], type_counts["server"] + type_counts["storage"], type_counts["camera"],
                  type_counts["access_point"]]
    donut_vals.append(sum(type_counts.values()) - sum(donut_vals))

    warranty = live.filter(status=Device.Status.ACTIVE, warranty_expiry__isnull=False,
                           warranty_expiry__lte=today + timedelta(days=90)
                           ).select_related("device_model").order_by("warranty_expiry")[:5]

    counters = _dashboard_counters()
    monitor_ids = list(Monitor.objects.filter(enabled=True).values_list("pk", flat=True))
    uptime_24h = monitor_stats.uptime(monitor_ids, 24)
    web_ids = list(Monitor.objects.filter(enabled=True, type__in=["http", "keyword"]).values_list("pk", flat=True))
    open_incidents = list(Incident.objects.filter(ended_at__isnull=True, kind=Incident.Kind.DOWN)
                          .select_related("monitor", "acknowledged_by"))
    for incident in open_incidents:
        incident.duration_label = human_duration(incident.duration)
        incident.suppressed_count = incident.suppressed.count()
    unreachable_root = next((i for i in open_incidents if i.suppressed_count), None)
    ctx = {
        **counters,
        "health": round(sum(uptime_24h.values()) / len(uptime_24h), 2) if uptime_24h else None,
        "up_count": counters["counts"]["up"],
        "devices_total": sum(type_counts.values()),
        "open_incidents": open_incidents,
        "unreachable_root": unreachable_root,
        "events": Incident.objects.select_related("monitor").order_by("-started_at")[:5],
        "ssl": Monitor.objects.filter(cert_expires_at__isnull=False).order_by("cert_expires_at")[:6],
        "web_avg_ms": monitor_stats.average_ms(web_ids),
        "warranty": warranty,
        "recent_audit": AuditLog.objects.select_related("object_type")[:6] if can(request.user, "audit") else [],
        "prefixes": _busiest_prefixes(),
        "maintenance_active": [w for w in MaintenanceWindow.objects.all() if w.is_active()],
        "chart_uptime": _uptime_chart(),
        "chart_response": monitor_stats.response_series(web_ids, hours=24, buckets=24),
        "has_web_monitors": bool(web_ids),
        "chart_types": {
            "labels": ["Switches", "Servers & storage", "Cameras", "Access points", "Other"],
            "values": donut_vals,
            "colors": ["--maint", "--up", "--warn", "--unreach", "--paused"],
        },
        "donut_legend": list(zip(["Switches", "Servers & storage", "Cameras", "Access points", "Other"],
                                 donut_vals, ["maint", "up", "warn", "unreach", "paused"], donut_keys, strict=True)),
    }
    return _page(request, "monitoring/dashboard.html", "dashboard", **ctx)


def _uptime_chart():
    data = monitor_stats.daily_uptime(14)
    values = [v for v in data["values"] if v is not None]
    # Default floor 95 %; go lower when a bad day would otherwise fall off the bottom of the chart.
    data["min"] = min(95, int(min(values)) - 1) if values else 95
    return data


def _busiest_prefixes(limit=6):
    from apps.ipam.views import _mgmt_ips, utilisation

    mgmt = _mgmt_ips()
    rows = list(Prefix.objects.exclude(status=Prefix.Status.DEPRECATED))
    for p in rows:
        p.used, p.pct = utilisation(p, mgmt)
    return sorted(rows, key=lambda p: -p.pct)[:limit]


def dashboard_live(request):
    """HTMX poll target: refreshed status counters."""
    return render(request, "partials/status_counters.html", _dashboard_counters())


# --------------------------------------------------------------------------
# Administration
# --------------------------------------------------------------------------


@capability_required("settings")
def settings_page(request, alert_form=None, tab=None, status=200):
    """Telegram alerts tab is real (apps/alerts); the other tabs are still the prototype."""
    from apps.alerts.views import settings_context

    tab = tab or request.GET.get("tab", "general")
    ctx = {"active": "settings", "users": fd.USERS, "tab": tab if tab in SETTINGS_TABS else "general",
           **settings_context(alert_form)}
    return render(request, "system/settings.html", ctx, status=status)


SETTINGS_TABS = ["general", "alerts", "monitoring", "security", "backups"]
