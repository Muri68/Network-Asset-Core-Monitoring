"""
UI prototype views. Every view renders fake data from fake_data.py.

Nothing here is persisted; forms are marked data-prototype and only show a toast.
These views are replaced app-by-app as the phases in PLAN.md land.
"""

from collections import Counter
from datetime import timedelta

from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

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


def _get(items, pk):
    try:
        return items[pk]
    except KeyError:
        raise Http404 from None


def _q(request):
    return request.GET.get("q", "").strip().lower()


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
# Monitoring
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Infrastructure
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# IPAM
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Security
# --------------------------------------------------------------------------


@capability_required("vault")
def vault(request):
    folder = request.GET.get("folder", "")
    q = _q(request)
    rows = [
        c for c in fd.CREDENTIALS
        if (not folder or str(c["folder_id"]) == folder)
        and (not q or q in c["name"].lower() or q in c["username"].lower() or q in c["host"].lower())
    ]
    return _page(request, "vault/vault.html", "vault", credentials=rows, folders=fd.VAULT_FOLDERS, folder=folder,
                 q=q, total=len(fd.CREDENTIALS), recent=fd.REVEAL_LOG[:5])


@capability_required("vault")
def credential_detail(request, pk):
    c = _get(fd.CREDENTIALS_BY_ID, pk)
    return _page(request, "vault/credential_detail.html", "vault", c=c,
                 log=[r for r in fd.REVEAL_LOG if r["credential"]["id"] == c["id"]],
                 history=[{"changed": timezone.now() - timedelta(days=c["age_days"]), "by": "Musa Ibrahim"},
                          {"changed": timezone.now() - timedelta(days=c["age_days"] + 92), "by": "Aisha Bello"}])


MAX_REVEAL_ATTEMPTS = 5


@capability_required("vault")
@require_POST
def credential_reveal(request, pk):
    """Prototype reveal flow: accepts fd.REVEAL_CODE, counts failures in a cookie."""
    c = _get(fd.CREDENTIALS_BY_ID, pk)
    failures = int(request.COOKIES.get("reveal_failures", "0"))
    if failures >= MAX_REVEAL_ATTEMPTS:
        response = render(request, "vault/partials/reveal_locked.html")
    elif request.POST.get("code") == fd.REVEAL_CODE:
        response = render(request, "vault/partials/reveal_secret.html", {"c": c})
        response.delete_cookie("reveal_failures")
    else:
        failures += 1
        template = "vault/partials/reveal_locked.html" if failures >= MAX_REVEAL_ATTEMPTS else \
            "vault/partials/reveal_form.html"
        response = render(request, template, {"c": c, "error": True,
                                              "remaining": MAX_REVEAL_ATTEMPTS - failures})
        response.set_cookie("reveal_failures", str(failures), max_age=600, samesite="Strict", httponly=True)
    response["Cache-Control"] = "no-store"
    return response


@capability_required("vault")
def reveal_log(request):
    log = fd.REVEAL_LOG
    if not can(request.user, "vault_logs_all"):
        log = [r for r in log if r["user"] == request.user.name]
    return _page(request, "vault/reveal_log.html", "vault", log=log, all_users=can(request.user, "vault_logs_all"))


# --------------------------------------------------------------------------
# Administration
# --------------------------------------------------------------------------


@capability_required("export")
def data(request):
    return _page(request, "system/data.html", "data", resources=fd.IMPORT_RESOURCES)


@capability_required("import")
@require_POST
def import_preview(request):
    rows = fd.IMPORT_PREVIEW
    return render(request, "system/partials/import_preview.html", {
        "rows": rows, "resource": request.POST.get("resource", "devices"),
        "creates": sum(1 for r in rows if r["action"] == "create"),
        "updates": sum(1 for r in rows if r["action"] == "update"),
        "errors": sum(1 for r in rows if r["action"] == "error"),
    })


@capability_required("settings")
def settings_page(request):
    return _page(request, "system/settings.html", "settings", users=fd.USERS)
