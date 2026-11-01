"""
UI prototype views. Every view renders fake data from fake_data.py.

Nothing here is persisted; forms are marked data-prototype and only show a toast.
These views are replaced app-by-app as the phases in PLAN.md land.
"""

import random
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
    counts = fd.status_counts()
    total = len(fd.MONITORS)
    return {"counts": counts, "total": total, "updated": timezone.now()}


def dashboard(request):
    rng = random.Random(7)
    today = timezone.localdate()
    uptime_days = [round(rng.uniform(99.55, 100), 2) for _ in range(14)]
    uptime_days[4], uptime_days[12], uptime_days[13] = 98.71, 99.1, 97.84
    response = [rng.randint(140, 260) for _ in range(24)]
    response[18] = 610

    live = Device.objects.exclude(status=Device.Status.RETIRED)
    type_counts = Counter(live.values_list("device_type", flat=True))
    donut_keys = ["switch", "server", "camera", "access_point", "other"]
    donut_vals = [type_counts["switch"], type_counts["server"] + type_counts["storage"], type_counts["camera"],
                  type_counts["access_point"]]
    donut_vals.append(sum(type_counts.values()) - sum(donut_vals))

    warranty = live.filter(status=Device.Status.ACTIVE, warranty_expiry__isnull=False,
                           warranty_expiry__lte=today + timedelta(days=90)
                           ).select_related("device_model").order_by("warranty_expiry")[:5]

    up_count = fd.status_counts()["up"]
    ctx = {
        **_dashboard_counters(),
        "health": round(sum(m["uptime_24h"] for m in fd.MONITORS) / len(fd.MONITORS), 2),
        "up_count": up_count,
        "devices_total": sum(type_counts.values()),
        "device_types": len(type_counts),
        "open_incidents": [i for i in fd.INCIDENTS if i["status"] == "open"],
        "alerts": fd.ALERTS[:5],
        "ssl": sorted((m for m in fd.MONITORS if m["cert_days"] is not None), key=lambda m: m["cert_days"]),
        "warranty": warranty,
        "recent_audit": AuditLog.objects.select_related("object_type")[:6] if can(request.user, "audit") else [],
        "prefixes": _busiest_prefixes(),
        "maintenance_active": [m for m in fd.MAINTENANCE if m["state"] == "active"],
        "chart_uptime": {
            "labels": [(today - timedelta(days=13 - i)).strftime("%d %b") for i in range(14)],
            "values": uptime_days, "min": 96,
        },
        "chart_response": {
            "labels": [f"{(timezone.localtime().hour - 23 + i) % 24:02d}:00" for i in range(24)],
            "values": response,
        },
        "chart_types": {
            "labels": ["Switches", "Servers & storage", "Cameras", "Access points", "Other"],
            "values": donut_vals,
            "colors": ["--maint", "--up", "--warn", "--unreach", "--paused"],
        },
        "donut_legend": list(zip(["Switches", "Servers & storage", "Cameras", "Access points", "Other"],
                                 donut_vals, ["maint", "up", "warn", "unreach", "paused"], donut_keys, strict=True)),
    }
    return _page(request, "monitoring/dashboard.html", "dashboard", **ctx)


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


def monitors(request):
    status = request.GET.get("status", "")
    mtype = request.GET.get("type", "")
    q = _q(request)
    rows = [
        m for m in fd.MONITORS
        if (not status or m["status"] == status)
        and (not mtype or m["type"] == mtype)
        and (not q or q in m["name"].lower() or q in m["target"].lower())
    ]
    order = {"down": 0, "unreachable": 1, "pending": 2, "maintenance": 3, "up": 4, "paused": 5}
    rows.sort(key=lambda m: (order[m["status"]], m["name"]))
    return _page(request, "monitoring/monitors.html", "monitors", monitors=rows, counts=fd.status_counts(),
                 status=status, mtype=mtype, q=q, types=fd.MONITOR_TYPES, total=len(fd.MONITORS))


def monitor_detail(request, pk):
    m = _get(fd.MONITORS_BY_ID, pk)
    chain = []
    parent = fd.MONITORS_BY_ID.get(m["parent_id"])
    while parent:
        chain.insert(0, parent)
        parent = fd.MONITORS_BY_ID.get(parent["parent_id"])
    children = [c for c in fd.MONITORS if c["parent_id"] == m["id"]]
    now = timezone.localtime()
    labels = [(now - timedelta(minutes=30 * (47 - i))).strftime("%H:%M") for i in range(48)]
    return _page(request, "monitoring/monitor_detail.html", "monitors", m=m, chain=chain, children=children,
                 incidents=[i for i in fd.INCIDENTS if i["monitor"]["id"] == m["id"]],
                 types=fd.MONITOR_TYPES, chart={"labels": labels, "values": m["series"]})


@capability_required("edit")
def monitor_form(request):
    return _page(request, "monitoring/monitor_form.html", "monitors", devices=fd.DEVICES, monitors=fd.MONITORS,
                 types=fd.MONITOR_TYPES)


def incidents(request):
    state = request.GET.get("state", "")
    rows = [i for i in fd.INCIDENTS if not state or i["status"] == state]
    return _page(request, "monitoring/incidents.html", "incidents", incidents=rows, state=state,
                 open_count=sum(1 for i in fd.INCIDENTS if i["status"] == "open"), alerts=fd.ALERTS)


def maintenance(request):
    return _page(request, "monitoring/maintenance.html", "maintenance", windows=fd.MAINTENANCE, devices=fd.DEVICES)


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
