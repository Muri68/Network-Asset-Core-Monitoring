"""
The monitor process (`manage.py run_monitor`). One asyncio loop:

    every TICK seconds: pick due monitors → push their next_check_at → run the checks concurrently
    → apply each result through the state machine → write a heartbeat.

Plus housekeeping every hour: daily uptime roll-ups and deleting raw results older than 30 days.
Database work runs in threads via sync_to_async; network checks run on the event loop.
"""

import asyncio
import datetime
import logging
import time

from asgiref.sync import sync_to_async
from django.db import close_old_connections, transaction
from django.db.models import Avg, Count, Q
from django.utils import timezone

from .checkers import run_check
from .models import CheckResult, DailyUptime, Monitor, MonitorHeartbeat, monitors_in_maintenance
from .state import apply_result

log = logging.getLogger("netcore.monitor")

TICK_SECONDS = 2
MAX_CONCURRENT = 100
RETENTION_DAYS = 30


def claim_due(now=None, limit=500):
    """Monitors due for a check; their next_check_at is pushed forward so they're not picked twice."""
    now = now or timezone.now()
    with transaction.atomic():
        due = list(Monitor.objects.select_for_update().filter(enabled=True, next_check_at__lte=now)
                   .order_by("next_check_at")[:limit])
        for m in due:
            Monitor.objects.filter(pk=m.pk).update(next_check_at=now + datetime.timedelta(seconds=m.interval_s))
    return due


async def run_checks(monitors):
    """Network part: run the checks concurrently (no database access). Returns [(monitor, result)]."""
    limiter = asyncio.Semaphore(MAX_CONCURRENT)

    async def one(monitor):
        async with limiter:
            return monitor, await run_check(monitor)

    return await asyncio.gather(*(one(m) for m in monitors))


def record_results(pairs, muted=None):
    """Database part: feed each result through the state machine."""
    muted = monitors_in_maintenance() if muted is None else muted
    for monitor, result in pairs:
        apply_result(monitor, result, in_maintenance=monitor.pk in muted)
    return len(pairs)


async def check_batch(monitors):
    """Run a batch of checks, then record the results. Returns how many ran."""
    if not monitors:
        return 0
    pairs = await run_checks(monitors)
    return await sync_to_async(record_results)(pairs)


def beat(started_at, checks_last_minute):
    MonitorHeartbeat.objects.update_or_create(pk=1, defaults={
        "last_beat": timezone.now(), "started_at": started_at, "checks_last_minute": checks_last_minute})


# --------------------------------------------------------------------------
# Housekeeping
# --------------------------------------------------------------------------


def rollup_day(day):
    """Summarise one day of results into DailyUptime rows (idempotent)."""
    tz = timezone.get_current_timezone()
    start = datetime.datetime.combine(day, datetime.time.min, tz)
    end = start + datetime.timedelta(days=1)
    rows = (CheckResult.objects.filter(checked_at__gte=start, checked_at__lt=end).values("monitor_id")
            .annotate(checks=Count("id"), failures=Count("id", filter=Q(ok=False)), avg_ms=Avg("response_ms")))
    for row in rows:
        times = sorted(CheckResult.objects.filter(monitor_id=row["monitor_id"], checked_at__gte=start,
                                                  checked_at__lt=end, response_ms__isnull=False)
                       .values_list("response_ms", flat=True))
        p95 = times[min(len(times) - 1, int(len(times) * 0.95))] if times else None
        uptime = (row["checks"] - row["failures"]) * 100 / row["checks"]
        DailyUptime.objects.update_or_create(monitor_id=row["monitor_id"], date=day, defaults={
            "checks": row["checks"], "failures": row["failures"], "uptime_pct": round(uptime, 3),
            "avg_ms": round(row["avg_ms"]) if row["avg_ms"] else None, "p95_ms": p95})
    return len(rows)


def housekeeping(now=None):
    """Roll up every finished day still in the raw data, then drop results past retention."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    oldest = CheckResult.objects.order_by("checked_at").values_list("checked_at", flat=True).first()
    if oldest:
        day = timezone.localdate(oldest)
        while day < today:
            rollup_day(day)
            day += datetime.timedelta(days=1)
    deleted, _ = CheckResult.objects.filter(checked_at__lt=now - datetime.timedelta(days=RETENTION_DAYS)).delete()
    return deleted


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------


async def run_forever(stop_event=None, tick=TICK_SECONDS):
    started_at = timezone.now()
    recent = []  # timestamps of finished checks, for the "checks per minute" figure
    last_housekeeping = 0.0
    log.info("Monitor process started")
    while stop_event is None or not stop_event.is_set():
        loop_start = time.monotonic()
        try:
            if time.monotonic() - last_housekeeping > 3600:
                await sync_to_async(housekeeping)()
                last_housekeeping = time.monotonic()
            due = await sync_to_async(claim_due)()
            ran = await check_batch(due)
            now = time.monotonic()
            recent = [t for t in recent if now - t < 60] + [now] * ran
            await sync_to_async(beat)(started_at, len(recent))
        except Exception:  # noqa: BLE001 — log and keep monitoring
            log.exception("Monitor loop error")
            await sync_to_async(close_old_connections)()
        await asyncio.sleep(max(0.0, tick - (time.monotonic() - loop_start)))
    log.info("Monitor process stopped")
