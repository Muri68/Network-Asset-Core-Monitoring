"""Figures for the monitor pages and dashboard, computed from CheckResult / DailyUptime."""

import datetime

from django.db.models import Avg, Count, Q
from django.utils import timezone

from .models import CheckResult, DailyUptime, Monitor


def uptime(monitor_ids, hours):
    """{monitor id: uptime % over the last `hours`} (None when there are no checks)."""
    since = timezone.now() - datetime.timedelta(hours=hours)
    rows = (CheckResult.objects.filter(monitor_id__in=monitor_ids, checked_at__gte=since).values("monitor_id")
            .annotate(total=Count("id"), good=Count("id", filter=Q(ok=True))))
    return {r["monitor_id"]: round(r["good"] * 100 / r["total"], 2) for r in rows if r["total"]}


def heartbeats(monitor, count=60):
    """Oldest → newest list of 'up'/'down' for the last `count` checks."""
    results = list(monitor.results.values_list("ok", flat=True)[:count])
    return ["up" if ok else "down" for ok in reversed(results)]


def heartbeats_for(monitors, count=36):
    return {m.pk: heartbeats(m, count) for m in monitors}


def response_series(monitor_ids, hours=24, buckets=48):
    """Average response time per time bucket (labels, values); None where there's no data."""
    now = timezone.now()
    size = datetime.timedelta(hours=hours) / buckets
    start = now - datetime.timedelta(hours=hours)
    sums = [0] * buckets
    counts = [0] * buckets
    for checked_at, ms in CheckResult.objects.filter(monitor_id__in=monitor_ids, checked_at__gte=start,
                                                     response_ms__isnull=False).values_list("checked_at",
                                                                                            "response_ms"):
        i = min(buckets - 1, int((checked_at - start) / size))
        sums[i] += ms
        counts[i] += 1
    labels = [timezone.localtime(start + size * (i + 1)).strftime("%H:%M") for i in range(buckets)]
    values = [round(s / c) if c else None for s, c in zip(sums, counts, strict=True)]
    # A new monitor has data only at the end: start the chart where the data starts (at least 6 buckets).
    first = next((i for i, v in enumerate(values) if v is not None), buckets)
    first = min(first, buckets - 6)
    return {"labels": labels[first:], "values": values[first:]}


def average_ms(monitor_ids, hours=24):
    since = timezone.now() - datetime.timedelta(hours=hours)
    value = CheckResult.objects.filter(monitor_id__in=monitor_ids, checked_at__gte=since,
                                       response_ms__isnull=False).aggregate(v=Avg("response_ms"))["v"]
    return round(value) if value else None


def daily_uptime(days=14):
    """Overall uptime per day for the dashboard bar chart: roll-ups for past days, live data for today."""
    today = timezone.localdate()
    out = []
    for offset in range(days - 1, -1, -1):
        day = today - datetime.timedelta(days=offset)
        if day == today:
            start = timezone.make_aware(datetime.datetime.combine(day, datetime.time.min))
            agg = CheckResult.objects.filter(checked_at__gte=start).aggregate(
                total=Count("id"), good=Count("id", filter=Q(ok=True)))
            value = round(agg["good"] * 100 / agg["total"], 2) if agg["total"] else None
        else:
            agg = DailyUptime.objects.filter(date=day).aggregate(checks=Count("id"), avg=Avg("uptime_pct"))
            value = round(float(agg["avg"]), 2) if agg["avg"] is not None else None
        out.append((day, value))
    return {"labels": [d.strftime("%d %b") for d, _ in out], "values": [v for _, v in out]}


def status_counts():
    counts = {s: 0 for s in ("up", "down", "unreachable", "maintenance", "pending", "paused", "unknown")}
    for row in Monitor.objects.values("status").annotate(n=Count("id")):
        counts[row["status"]] = row["n"]
    return counts
