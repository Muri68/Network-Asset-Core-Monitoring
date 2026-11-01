"""Alert message texts (PLAN.md §7.2), in Telegram's HTML formatting."""

import html

from django.utils import timezone

from apps.monitoring.models import Incident, Monitor, Status, human_duration


def esc(value):
    return html.escape(str(value), quote=False)


def when(dt):
    """Local time; the date too unless it's today."""
    local = timezone.localtime(dt)
    if local.date() == timezone.localdate():
        return local.strftime("%H:%M")
    return local.strftime("%d %b %Y %H:%M")


def what(monitor):
    """e.g. 'ping 10.0.0.1', 'tcp 10.0.0.1:22', 'http https://portal.example'."""
    target = f"{monitor.target}:{monitor.port}" if monitor.type == Monitor.Type.TCP and monitor.port else monitor.target
    return f"{monitor.type} {target}"


def _affected(incident):
    count = Incident.objects.filter(suppressed_by=incident, ended_at__isnull=True).count()
    return f"\nAffected: {count} monitor{'s' if count != 1 else ''} unreachable behind it" if count else ""


def _device(monitor):
    device = monitor.device
    return f"\nDevice: {esc(device.name)} · {esc(device.location)}" if device else ""


def down(incident):
    m = incident.monitor
    return (f"🔴 <b>DOWN: {esc(m.name)}</b> ({esc(what(m))})\n"
            f"Cause: {esc(incident.cause)}\n"
            f"Since: {when(incident.started_at)}{_device(m)}{_affected(incident)}")


def reminder(incident):
    m = incident.monitor
    return (f"🔴 <b>STILL DOWN: {esc(m.name)}</b> — for {human_duration(incident.duration)}\n"
            f"Cause: {esc(incident.cause)}\n"
            f"Since: {when(incident.started_at)}{_affected(incident)}\n"
            f"<i>Acknowledge the incident in NetCore to stop reminders.</i>")


def recovered(incident):
    m = incident.monitor
    was = human_duration(incident.duration)
    if m.status == Status.PAUSED:
        return f"⏸ <b>PAUSED: {esc(m.name)}</b> — monitoring paused after {was} down"
    return f"🟢 <b>UP: {esc(m.name)}</b> — was down {was}"


def ssl_expiring(monitor):
    days = monitor.cert_days
    date = timezone.localtime(monitor.cert_expires_at).strftime("%d %b %Y")
    if days < 0:
        return f"🔴 <b>SSL certificate EXPIRED: {esc(monitor.name)}</b>\n{esc(monitor.target)} expired on {date}"
    left = "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
    return f"🟠 <b>SSL certificate expiring: {esc(monitor.name)}</b>\n{esc(monitor.target)} expires {left} ({date})"


def test(user):
    return f"✅ <b>Test alert from NetCore</b>\nHi {esc(user.name)}, your NetCore alerts will arrive in this chat."


def delayed(created_at):
    return f"⏳ <i>Delayed — originally at {when(created_at)}</i>\n"


def status_summary():
    """Reply to /status: counts, then what's down."""
    counts = dict.fromkeys(["up", "down", "unreachable", "pending", "maintenance"], 0)
    for status in Monitor.objects.filter(enabled=True).values_list("status", flat=True):
        if status in counts:
            counts[status] += 1
    lines = [f"🟢 {counts['up']} up · 🔴 {counts['down']} down · 🟣 {counts['unreachable']} unreachable · "
             f"🟡 {counts['pending']} pending · 🔧 {counts['maintenance']} maintenance"]
    open_down = (Incident.objects.filter(ended_at__isnull=True, kind=Incident.Kind.DOWN)
                 .select_related("monitor").order_by("started_at")[:10])
    for incident in open_down:
        lines.append(f"🔴 {esc(incident.monitor.name)} — {human_duration(incident.duration)}: {esc(incident.cause)}")
    if not open_down:
        lines.append("Nothing is down. 👍")
    return "\n".join(lines)
