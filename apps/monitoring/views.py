from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.decorators import capability_required
from apps.audit.log import log_event
from apps.inventory.models import Device

from . import stats
from .forms import MaintenanceForm, MonitorForm
from .models import Incident, MaintenanceWindow, Monitor, Status, human_duration
from .state import children_map, pause, resume

PAGE_SIZE = 50
STATUS_ORDER = ["down", "unreachable", "pending", "maintenance", "unknown", "up", "paused"]


def monitors(request):
    status = request.GET.get("status", "")
    mtype = request.GET.get("type", "")
    q = request.GET.get("q", "").strip()
    qs = Monitor.objects.select_related("device")
    if status in Status.values:
        qs = qs.filter(status=status)
    if mtype in Monitor.Type.values:
        qs = qs.filter(type=mtype)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(target__icontains=q) | Q(device__name__icontains=q))
    rows = sorted(qs, key=lambda m: (STATUS_ORDER.index(m.status), m.name.lower()))
    page = Paginator(rows, PAGE_SIZE).get_page(request.GET.get("page"))
    ids = [m.pk for m in page.object_list]
    up24 = stats.uptime(ids, 24)
    beats = stats.heartbeats_for(page.object_list, 36)
    for m in page.object_list:
        m.uptime_24h = up24.get(m.pk)
        m.beats = beats[m.pk]
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "monitoring/monitors.html", {
        "page": page, "monitors": page.object_list, "counts": stats.status_counts(), "status": status,
        "mtype": mtype, "q": q, "types": Monitor.Type.choices, "total": Monitor.objects.count(),
        "query": query.urlencode(), "active": "monitors",
    })


def monitor_detail(request, pk):
    m = get_object_or_404(Monitor.objects.select_related("device", "parent"), pk=pk)
    children_ids = children_map().get(m.pk, [])
    open_incident = m.incidents.filter(ended_at__isnull=True).first()
    return render(request, "monitoring/monitor_detail.html", {
        "m": m,
        "chain": list(reversed(m.ancestors())),
        "children": Monitor.objects.filter(pk__in=children_ids),
        "incidents": m.incidents.select_related("acknowledged_by")[:20],
        "open_incident": open_incident,
        "beats": stats.heartbeats(m, 60),
        "uptime": {name: stats.uptime([m.pk], hours).get(m.pk)
                   for name, hours in (("day", 24), ("week", 24 * 7), ("month", 24 * 30))},
        "avg_ms": stats.average_ms([m.pk]),
        "chart": stats.response_series([m.pk]),
        "down_for": human_duration(open_incident.duration) if open_incident else None,
        "active": "monitors",
    })


def _form_page(request, form, monitor=None):
    return render(request, "monitoring/monitor_form.html", {
        "form": form, "monitor": monitor, "active": "monitors",
    }, status=400 if form.is_bound else 200)


@capability_required("edit")
def monitor_create(request):
    if request.method == "POST":
        form = MonitorForm(request.POST)
        if form.is_valid():
            monitor = form.save()
            messages.success(request, f"{monitor} added — the first check runs within a few seconds "
                                      "(if the monitor process is running).")
            return redirect(monitor)
        return _form_page(request, form)
    initial = {}
    device = Device.objects.filter(pk=request.GET.get("device") or 0).first()
    if device:
        initial = {"device": device.pk, "name": device.name, "type": "ping", "target": device.mgmt_ip or ""}
    return _form_page(request, MonitorForm(initial=initial))


@capability_required("edit")
def monitor_edit(request, pk):
    monitor = get_object_or_404(Monitor, pk=pk)
    form = MonitorForm(request.POST or None, instance=monitor)
    if request.method == "POST" and form.is_valid():
        monitor = form.save(commit=False)
        monitor.next_check_at = timezone.now()
        monitor.save()
        messages.success(request, f"{monitor} saved.")
        return redirect(monitor)
    return _form_page(request, form, monitor)


@capability_required("edit")
@require_POST
def monitor_delete(request, pk):
    monitor = get_object_or_404(Monitor, pk=pk)
    monitor.delete()
    messages.success(request, f"{monitor} deleted with its check history.")
    return redirect("monitoring:monitors")


@capability_required("edit")
@require_POST
def monitor_action(request, pk, action):
    monitor = get_object_or_404(Monitor, pk=pk)
    if action == "pause":
        pause(monitor)
        messages.success(request, f"{monitor} paused — no checks or alerts until resumed.")
    elif action == "resume":
        resume(monitor)
        messages.success(request, f"{monitor} resumed.")
    elif action == "check":
        Monitor.objects.filter(pk=pk).update(next_check_at=timezone.now())
        messages.success(request, f"{monitor} will be checked within a few seconds.")
    return redirect(request.POST.get("next") or monitor.get_absolute_url())


# --------------------------------------------------------------------------
# Incidents
# --------------------------------------------------------------------------


def incidents(request):
    state = request.GET.get("state", "")
    qs = Incident.objects.select_related("monitor", "acknowledged_by", "suppressed_by__monitor")
    if state == "open":
        qs = qs.filter(ended_at__isnull=True)
    elif state == "resolved":
        qs = qs.filter(ended_at__isnull=False)
    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))
    for i in page.object_list:
        i.duration_label = human_duration(i.duration)
        i.suppressed_count = i.suppressed.count()
    return render(request, "monitoring/incidents.html", {
        "page": page, "incidents": page.object_list, "state": state,
        "open_count": Incident.objects.filter(ended_at__isnull=True).count(), "active": "incidents",
    })


@capability_required("edit")
@require_POST
def incident_ack(request, pk):
    incident = get_object_or_404(Incident, pk=pk)
    if incident.acknowledged_by_id is None:
        incident.acknowledged_by = request.user
        incident.acknowledged_at = timezone.now()
        incident.save(update_fields=["acknowledged_by", "acknowledged_at"])
        log_event("incident_ack", incident.monitor, message=f"Incident #{incident.pk}: {incident.cause}")
        messages.success(request, f"Incident #{incident.pk} acknowledged.")
    return redirect(request.POST.get("next") or "monitoring:incidents")


# --------------------------------------------------------------------------
# Maintenance windows
# --------------------------------------------------------------------------


def maintenance(request):
    windows = list(MaintenanceWindow.objects.prefetch_related("devices", "monitors").select_related("created_by"))
    order = {"active": 0, "scheduled": 1, "completed": 2}
    windows.sort(key=lambda w: (order[w.state], w.starts_at))
    return render(request, "monitoring/maintenance.html", {"windows": windows, "active": "maintenance"})


def _maintenance_form(request, form, window=None):
    return render(request, "monitoring/maintenance_form.html", {
        "form": form, "window": window, "time_zone": settings.TIME_ZONE, "active": "maintenance",
    }, status=400 if form.is_bound else 200)


@capability_required("edit")
def maintenance_create(request):
    initial = {}
    device = Device.objects.filter(pk=request.GET.get("device") or 0).first()
    if device:
        initial["devices"] = [device.pk]
        initial["name"] = f"{device.name} maintenance"
    form = MaintenanceForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        window = form.save()
        messages.success(request, f"Maintenance window “{window}” scheduled.")
        return redirect("monitoring:maintenance")
    return _maintenance_form(request, form)


@capability_required("edit")
def maintenance_edit(request, pk):
    window = get_object_or_404(MaintenanceWindow, pk=pk)
    form = MaintenanceForm(request.POST or None, instance=window)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"“{window}” saved.")
        return redirect("monitoring:maintenance")
    return _maintenance_form(request, form, window)


@capability_required("edit")
@require_POST
def maintenance_end(request, pk):
    window = get_object_or_404(MaintenanceWindow, pk=pk)
    now = timezone.now()
    window.ended_early_at = now  # ends this occurrence only; later ones still apply
    window.save(update_fields=["ended_early_at"])
    # Re-check muted monitors straight away so they leave the maintenance state.
    Monitor.objects.filter(status=Status.MAINTENANCE).update(next_check_at=now)
    messages.success(request, f"“{window}” ended.")
    return redirect("monitoring:maintenance")


@capability_required("edit")
@require_POST
def maintenance_delete(request, pk):
    window = get_object_or_404(MaintenanceWindow, pk=pk)
    window.delete()
    Monitor.objects.filter(status=Status.MAINTENANCE).update(next_check_at=timezone.now())
    messages.success(request, f"“{window}” deleted.")
    return redirect("monitoring:maintenance")
