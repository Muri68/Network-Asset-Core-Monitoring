import csv
import datetime

from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache

from apps.accounts.decorators import capability_required

from .actions import ACTIONS
from .log import log_event
from .models import AuditLog

PAGE_SIZE = 50


def _parse_date(value):
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _filtered(request):
    """Apply the filters in the query string; returns (queryset, filters dict)."""
    f = {
        "q": request.GET.get("q", "").strip(),
        "actor": request.GET.get("actor", ""),
        "action": request.GET.get("action", ""),
        "date_from": _parse_date(request.GET.get("from")),
        "date_to": _parse_date(request.GET.get("to")),
    }
    qs = AuditLog.objects.select_related("object_type")
    if f["q"]:
        from django.db.models import Q

        qs = qs.filter(Q(object_repr__icontains=f["q"]) | Q(actor_repr__icontains=f["q"]) |
                       Q(message__icontains=f["q"]) | Q(ip__icontains=f["q"]))
    if f["actor"]:
        qs = qs.filter(actor_repr=f["actor"])
    if f["action"] in ACTIONS:
        qs = qs.filter(action=f["action"])
    tz = timezone.get_current_timezone()
    if f["date_from"]:
        qs = qs.filter(timestamp__gte=datetime.datetime.combine(f["date_from"], datetime.time.min, tz))
    if f["date_to"]:
        qs = qs.filter(timestamp__lt=datetime.datetime.combine(f["date_to"] + datetime.timedelta(days=1),
                                                              datetime.time.min, tz))
    return qs, f


@capability_required("audit")
@never_cache
def audit_log(request):
    qs, filters = _filtered(request)
    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "system/audit.html", {
        "page": page,
        "entries": page.object_list,
        "filters": filters,
        "query": query.urlencode(),
        "actions": [(k, v[0]) for k, v in ACTIONS.items()],
        "actors": AuditLog.objects.order_by("actor_repr").values_list("actor_repr", flat=True).distinct(),
        "total": AuditLog.objects.count(),
        "active": "audit",
    })


@capability_required("audit")
def audit_export(request):
    qs, filters = _filtered(request)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    response["Content-Disposition"] = f'attachment; filename="netcore-audit-{stamp}.csv"'
    writer = csv.writer(response)
    writer.writerow(["time", "user", "action", "object", "message", "changes", "ip", "browser"])
    count = 0
    for e in qs.iterator():
        changes = "; ".join(f"{k}: {v[0]} → {v[1]}" for k, v in e.changes.items()) if e.changes else ""
        writer.writerow([timezone.localtime(e.timestamp).isoformat(timespec="seconds"), e.actor_repr, e.label,
                         e.object_label, e.message, changes, e.ip or "", e.user_agent])
        count += 1
    active = {k: str(v) for k, v in filters.items() if v}
    log_event("export", message=f"Audit log CSV, {count} rows" + (f", filters: {active}" if active else ""))
    return response
