"""
The monitoring state machine (PLAN.md §7.1, step 4). Synchronous and database-only, so every rule is
testable without a network:

- success → UP; a DOWN/UNREACHABLE monitor recovers and its incident is closed;
- failure → PENDING until `retries` failures in a row, then:
    in a maintenance window          → MAINTENANCE, no incident
    an ancestor is DOWN/UNREACHABLE  → UNREACHABLE, incident linked to the ancestor's
    an ancestor is still PENDING     → stay PENDING one more cycle (the parent decides first)
    otherwise                        → DOWN, incident opened
- when a monitor goes DOWN, children already DOWN become UNREACHABLE under its incident;
- when a parent recovers, children still failing become DOWN in their own right.

Every status change is announced with the `status_changed` signal (Telegram alerts hook in here).
"""

from django.db import transaction
from django.dispatch import Signal
from django.utils import timezone

from .models import CheckResult, Incident, Monitor, Status

# sender=Monitor, monitor=…, old=Status, new=Status, incident=Incident|None
status_changed = Signal()

FAILED = (Status.DOWN, Status.UNREACHABLE)


def describe(result, monitor):
    """Cause shown to people, e.g. 'Timeout — no reply after 3 checks'."""
    if result.error_class == "timeout":
        return f"{result.message} — {monitor.retries} failed checks in a row"
    return result.message or "Check failed"


def _open_incident(monitor):
    return monitor.incidents.filter(ended_at__isnull=True).order_by("-started_at").first()


def _set_status(monitor, new, now, incident=None):
    old = monitor.status
    if old == new:
        return
    monitor.status = new
    monitor.last_change_at = now
    transaction.on_commit(lambda: status_changed.send(Monitor, monitor=monitor, old=old, new=new,
                                                      incident=incident))


def _root_failure(monitor):
    """(state, ancestor) for the nearest ancestor that is failing or pending, else (None, None)."""
    for ancestor in monitor.ancestors():
        if ancestor.status in FAILED:
            return "failed", ancestor
        if ancestor.status == Status.PENDING:
            return "pending", ancestor
    return None, None


@transaction.atomic
def apply_result(monitor, result, *, now=None, in_maintenance=False):
    """Record `result` for `monitor` and move it to its new status. Returns the monitor."""
    now = now or timezone.now()
    monitor = Monitor.objects.select_for_update().get(pk=monitor.pk)
    CheckResult.objects.create(monitor=monitor, checked_at=now, ok=result.ok, response_ms=result.response_ms,
                               status_code=result.status_code, error_class=result.error_class,
                               message=result.message)
    monitor.last_check_at = now
    monitor.last_response_ms = result.response_ms if result.ok else None
    if result.cert_expires_at:
        monitor.cert_expires_at = result.cert_expires_at

    if result.ok:
        monitor.consecutive_failures = 0
        monitor.last_error = ""
        incident = _open_incident(monitor)
        if incident:
            incident.ended_at = now
            incident.save(update_fields=["ended_at"])
        _set_status(monitor, Status.MAINTENANCE if in_maintenance else Status.UP, now, incident)
        monitor.save()
        if incident:
            _promote_children(monitor, now)
        return monitor

    monitor.consecutive_failures = min(monitor.consecutive_failures + 1, 999)
    monitor.last_error = result.message[:255]
    cause = describe(result, monitor)

    if in_maintenance:
        _set_status(monitor, Status.MAINTENANCE, now)
    elif monitor.status in FAILED:
        _reconsider_failed(monitor, result, now)
    elif monitor.consecutive_failures < monitor.retries:
        _set_status(monitor, Status.PENDING, now)
    else:
        state, ancestor = _root_failure(monitor)
        if state == "pending":
            _set_status(monitor, Status.PENDING, now)  # wait: the parent will decide first
        elif state == "failed":
            parent_incident = _open_incident(ancestor)
            incident = Incident.objects.create(
                monitor=monitor, kind=Incident.Kind.UNREACHABLE, started_at=now, error_class="unreachable_via_parent",
                cause=f"Unreachable — {ancestor} is down", suppressed_by=parent_incident and (
                    parent_incident.suppressed_by or parent_incident),
            )
            _set_status(monitor, Status.UNREACHABLE, now, incident)
        else:
            incident = Incident.objects.create(monitor=monitor, kind=Incident.Kind.DOWN, started_at=now,
                                               error_class=result.error_class, cause=cause)
            _set_status(monitor, Status.DOWN, now, incident)
            monitor.save()
            _demote_children(monitor, incident, now)
            return monitor
    monitor.save()
    return monitor


def _reconsider_failed(monitor, result, now):
    """Already DOWN/UNREACHABLE and failed again: if it was only unreachable and its parent is back, it's down now."""
    if monitor.status != Status.UNREACHABLE:
        return
    state, _ancestor = _root_failure(monitor)
    if state is not None:
        return
    incident = _open_incident(monitor)
    if incident:
        incident.ended_at = now
        incident.save(update_fields=["ended_at"])
    new = Incident.objects.create(monitor=monitor, kind=Incident.Kind.DOWN, started_at=now,
                                  error_class=result.error_class,
                                  cause=f"Still failing after its parent recovered: {describe(result, monitor)}")
    _set_status(monitor, Status.DOWN, now, new)


def children_map():
    """{monitor id: [child monitor ids]} using effective parents, built with two queries."""
    monitors = list(Monitor.objects.filter(enabled=True).values("pk", "parent_id", "device_id", "device__parent_id"))
    first_by_device = {}
    for m in sorted(monitors, key=lambda m: m["pk"]):
        if m["device_id"]:
            first_by_device.setdefault(m["device_id"], m["pk"])
    children = {}
    for m in monitors:
        parent = m["parent_id"] or first_by_device.get(m["device__parent_id"])
        if parent and parent != m["pk"]:
            children.setdefault(parent, []).append(m["pk"])
    return children


def _descendants(monitor):
    """Monitors whose effective parent chain includes `monitor`."""
    children, found, frontier = children_map(), set(), [monitor.pk]
    while frontier:
        nxt = []
        for pk in frontier:
            for child in children.get(pk, []):
                if child not in found and child != monitor.pk:
                    found.add(child)
                    nxt.append(child)
        frontier = nxt
    return list(Monitor.objects.filter(pk__in=found))


def _demote_children(monitor, incident, now):
    """Children that already went DOWN on their own become UNREACHABLE under the parent's incident."""
    for child in _descendants(monitor):
        if child.status != Status.DOWN:
            continue
        child_incident = _open_incident(child)
        if child_incident:
            child_incident.kind = Incident.Kind.UNREACHABLE
            child_incident.suppressed_by = incident
            child_incident.cause = f"Unreachable — {monitor} is down"
            child_incident.save(update_fields=["kind", "suppressed_by", "cause"])
        _set_status(child, Status.UNREACHABLE, now, child_incident)
        child.save(update_fields=["status", "last_change_at"])


def _promote_children(monitor, now):
    """After a recovery, children still marked UNREACHABLE get re-checked straight away."""
    for child in _descendants(monitor):
        if child.status == Status.UNREACHABLE:
            Monitor.objects.filter(pk=child.pk).update(next_check_at=now)


def pause(monitor, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        monitor.enabled = False
        incident = _open_incident(monitor)
        if incident:
            incident.ended_at = now
            incident.save(update_fields=["ended_at"])
        _set_status(monitor, Status.PAUSED, now)
        monitor.consecutive_failures = 0
        monitor.save()


def resume(monitor, now=None):
    now = now or timezone.now()
    monitor.enabled = True
    monitor.next_check_at = now
    monitor.consecutive_failures = 0
    _set_status(monitor, Status.UNKNOWN, now)
    monitor.save()

