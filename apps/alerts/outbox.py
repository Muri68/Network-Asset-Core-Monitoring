"""
What to send, to whom, and the bookkeeping of sending it (PLAN.md §7.2). All database work, no network.

`sweep()` runs every few seconds in `run_monitor` and compares incidents with the alerts already queued:

- an open DOWN incident with no "down" alert yet               → DOWN to every recipient
- an open DOWN incident, unacknowledged, every reminder period  → STILL DOWN
- a closed incident whose DOWN was delivered, no "recovered"    → UP (or PAUSED) to those who got the DOWN
- an SSL certificate within its warning days                    → one warning a day
- unsent alerts for incidents that became UNREACHABLE (the parent's alert covers them) are cancelled

Nothing is sent for a monitor while it is in a maintenance window; the UP after a window waits until it ends.
Comparing state instead of reacting to signals means an alert is never missed, whichever process changed
the monitor, and the unique `dedupe_key` means it is never sent twice.
"""

import datetime
import hashlib
import secrets

from django.db import transaction
from django.utils import timezone

from apps.accounts import roles
from apps.accounts.models import Profile, User
from apps.audit.log import log_event
from apps.monitoring.models import Incident, Monitor, Status

from . import compose
from .models import AlertSettings, Notification

GIVE_UP_AFTER = datetime.timedelta(hours=24)
DELAYED_AFTER = datetime.timedelta(minutes=2)
LOOKBACK = datetime.timedelta(hours=24)  # older incidents don't start new alerts
LINK_VALID_FOR = datetime.timedelta(minutes=15)
SSL_WARNING_HOUR = 8  # local time; one warning a day, not at midnight


def recipients(admins_only=False):
    """Active Admins and Engineers with a linked chat and alerts not muted (PLAN.md A6)."""
    role_names = [roles.ADMIN] if admins_only else [roles.ADMIN, roles.ENGINEER]
    return list(User.objects.filter(is_active=True, groups__name__in=role_names,
                                    profile__telegram_chat_id__isnull=False, profile__alerts_muted=False)
                .distinct().select_related("profile"))


def queue(users, event, text, *, subject, incident=None, monitor=None, sequence=0, now=None):
    """Queue one alert per user; already-queued ones are skipped. Returns how many were added."""
    now = now or timezone.now()
    added = 0
    for user in users:
        _obj, created = Notification.objects.get_or_create(
            dedupe_key=f"{event}:{subject}:{sequence}:{user.pk}",
            defaults={"user": user, "event": event, "text": text, "incident": incident, "monitor": monitor,
                      "sequence": sequence, "created_at": now, "next_attempt_at": now})
        added += created
    return added


# --------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------


def sweep(now=None):
    """Queue whatever is due. Returns the number of alerts added."""
    alert_settings = AlertSettings.load()
    if not alert_settings.active:
        return 0
    now = now or timezone.now()
    with transaction.atomic():
        _cancel_unneeded()
        users = recipients()
        added = _queue_down(users, now) + _queue_recovered(now)
        if alert_settings.reminder_minutes:
            added += _queue_reminders(users, alert_settings.reminder_minutes, now)
        if alert_settings.ssl_warnings:
            added += _queue_ssl(users, now)
    return added


def _cancel_unneeded():
    pending = Notification.objects.filter(status=Notification.State.PENDING)
    pending.filter(incident__kind=Incident.Kind.UNREACHABLE).update(
        status=Notification.State.CANCELLED, error="Its parent went down; the parent's alert covers it")
    pending.filter(event=Notification.Event.REMINDER, incident__ended_at__isnull=False).update(
        status=Notification.State.CANCELLED, error="Recovered before the reminder could be sent")


def _queue_down(users, now):
    if not users:
        return 0
    incidents = (Incident.objects.filter(kind=Incident.Kind.DOWN, ended_at__isnull=True, started_at__gte=now - LOOKBACK)
                 .exclude(monitor__status=Status.MAINTENANCE)
                 .exclude(notifications__event=Notification.Event.DOWN)
                 .select_related("monitor__device__location"))
    return sum(queue(users, Notification.Event.DOWN, compose.down(i), subject=f"i{i.pk}", incident=i,
                     monitor=i.monitor, now=now) for i in incidents)


def _queue_recovered(now):
    incidents = (Incident.objects.filter(ended_at__isnull=False, ended_at__gte=now - LOOKBACK,
                                         notifications__event=Notification.Event.DOWN,
                                         notifications__status=Notification.State.SENT)
                 .exclude(monitor__status__in=[Status.MAINTENANCE, Status.DOWN, Status.UNREACHABLE, Status.PENDING])
                 .exclude(notifications__event=Notification.Event.RECOVERED)
                 .select_related("monitor").distinct())
    added = 0
    for incident in incidents:
        if incident.monitor.incidents.filter(started_at__gte=incident.ended_at).exists():
            continue  # failed again since: the newer incident sends its own alerts
        told = [u for u in recipients() if incident.notifications.filter(
            user=u, event=Notification.Event.DOWN, status=Notification.State.SENT).exists()]
        added += queue(told, Notification.Event.RECOVERED, compose.recovered(incident), subject=f"i{incident.pk}",
                       incident=incident, monitor=incident.monitor, now=now)
    return added


def _queue_reminders(users, minutes, now):
    if not users:
        return 0
    period = datetime.timedelta(minutes=minutes)
    incidents = (Incident.objects.filter(kind=Incident.Kind.DOWN, ended_at__isnull=True, acknowledged_at__isnull=True,
                                         started_at__lte=now - period, monitor__status=Status.DOWN,
                                         notifications__event=Notification.Event.DOWN)
                 .select_related("monitor").distinct())
    added = 0
    for incident in incidents:
        number = int((now - incident.started_at) / period)
        added += queue(users, Notification.Event.REMINDER, compose.reminder(incident), subject=f"i{incident.pk}",
                       incident=incident, monitor=incident.monitor, sequence=number, now=now)
    return added


def _queue_ssl(users, now):
    local = timezone.localtime(now)
    if not users or local.hour < SSL_WARNING_HOUR:
        return 0
    monitors = (Monitor.objects.filter(enabled=True, cert_expires_at__isnull=False,
                                       cert_expires_at__lte=now + datetime.timedelta(days=366))
                .exclude(status=Status.MAINTENANCE))
    day = local.date().toordinal()
    return sum(queue(users, Notification.Event.SSL, compose.ssl_expiring(m), subject=f"m{m.pk}", monitor=m,
                     sequence=day, now=now)
               for m in monitors if m.cert_days <= m.ssl_warn_days)


def notify_admins(text, *, subject, now=None):
    """A one-off notice to every Admin with Telegram linked (e.g. a locked vault, Phase 7)."""
    if not AlertSettings.load().active:
        return 0
    return queue(recipients(admins_only=True), Notification.Event.NOTICE, text, subject=subject, now=now)


# --------------------------------------------------------------------------
# Delivery bookkeeping (the sending itself is in worker.py)
# --------------------------------------------------------------------------


def due(now=None, limit=30):
    """Pending alerts ready to send, as (notification, chat id, text). Expired or unneeded ones are closed."""
    now = now or timezone.now()
    ready = []
    pending = (Notification.objects.filter(status=Notification.State.PENDING, next_attempt_at__lte=now)
               .select_related("user__profile").order_by("created_at")[:limit])
    for n in pending:
        profile = n.user.profile
        if now - n.created_at > GIVE_UP_AFTER:
            _close(n, Notification.State.FAILED, n.error or "Not delivered within 24 hours")
        elif not (n.user.is_active and profile.telegram_chat_id) or (profile.alerts_muted and n.event != "test"):
            _close(n, Notification.State.CANCELLED, "Recipient muted alerts or unlinked Telegram")
        else:
            text = compose.delayed(n.created_at) + n.text if now - n.created_at > DELAYED_AFTER else n.text
            ready.append((n, profile.telegram_chat_id, text))
    return ready


def _close(notification, status, error=""):
    notification.status = status
    notification.error = error[:255]
    notification.save(update_fields=["status", "error"])


def mark_sent(notification, now=None):
    notification.status = Notification.State.SENT
    notification.sent_at = now or timezone.now()
    notification.attempts += 1
    notification.error = ""
    notification.save(update_fields=["status", "sent_at", "attempts", "error"])


def mark_failed(notification, error, now=None):
    """Retry with back-off (30 s, 1 min, 2 min … up to 30 min), unless the error can't be fixed by retrying."""
    now = now or timezone.now()
    notification.attempts += 1
    notification.error = str(error)[:255]
    if getattr(error, "permanent", False):
        notification.status = Notification.State.FAILED
    else:
        wait = getattr(error, "retry_after", None) or min(30 * 2 ** (notification.attempts - 1), 1800)
        notification.next_attempt_at = now + datetime.timedelta(seconds=wait)
    notification.save(update_fields=["status", "attempts", "error", "next_attempt_at"])


# --------------------------------------------------------------------------
# Linking a Telegram chat to a user
# --------------------------------------------------------------------------


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def start_link(user, now=None):
    """A new one-time token for the deep link https://t.me/<bot>?start=<token>. Returns (token, expires)."""
    now = now or timezone.now()
    token = secrets.token_urlsafe(24)
    profile = user.profile
    profile.telegram_link_hash = _hash(token)
    profile.telegram_link_expires = now + LINK_VALID_FOR
    profile.save(update_fields=["telegram_link_hash", "telegram_link_expires"])
    return token, profile.telegram_link_expires


def unlink(user, actor=None):
    profile = user.profile
    if not profile.telegram_chat_id:
        return
    profile.telegram_chat_id = None
    profile.telegram_name = ""
    profile.telegram_linked_at = None
    profile.save(update_fields=["telegram_chat_id", "telegram_name", "telegram_linked_at"])
    log_event("telegram_unlink", user, actor=actor or user)


def unlink_everyone(actor):
    """When the bot changes, old chats can't be reached by the new bot: everyone links again."""
    count = Profile.objects.filter(telegram_chat_id__isnull=False).update(
        telegram_chat_id=None, telegram_name="", telegram_linked_at=None)
    if count:
        log_event("telegram_unlink", actor=actor, message=f"Bot changed: {count} Telegram link(s) removed")
    return count


HELP = ("NetCore alerts bot.\n/status — what's up and down right now\n"
        "To link this chat, open your NetCore profile and press <b>Link Telegram</b>.")


def handle_update(update, now=None):
    """One incoming Telegram message → (chat id, reply text) or None. Only private chats are answered."""
    now = now or timezone.now()
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    if chat.get("type") != "private" or "id" not in chat:
        return None
    chat_id = str(chat["id"])
    text = (message.get("text") or "").strip()
    command, _space, argument = text.partition(" ")
    command = command.split("@")[0].lower()

    if command == "/start" and argument:
        return chat_id, _link(chat_id, argument.strip(), message.get("from") or {}, now)
    linked = Profile.objects.filter(telegram_chat_id=chat_id, user__is_active=True).select_related("user").first()
    if command == "/status":
        return chat_id, compose.status_summary() if linked else "This chat isn't linked to NetCore yet.\n\n" + HELP
    if linked:
        return chat_id, f"Linked to NetCore as {compose.esc(linked.user.name)}.\n" + HELP
    return chat_id, HELP


def _link(chat_id, token, sender, now):
    profile = (Profile.objects.filter(telegram_link_hash=_hash(token), telegram_link_expires__gt=now,
                                      user__is_active=True).select_related("user").first())
    if not profile:
        return ("This link has expired or was already used. Open your NetCore profile and press "
                "<b>Link Telegram</b> again.")
    name = " ".join(filter(None, [sender.get("first_name"), sender.get("last_name")]))
    if sender.get("username"):
        name = f"{name} (@{sender['username']})".strip()
    with transaction.atomic():
        Profile.objects.filter(telegram_chat_id=chat_id).exclude(pk=profile.pk).update(
            telegram_chat_id=None, telegram_name="", telegram_linked_at=None)
        profile.telegram_chat_id = chat_id
        profile.telegram_name = name[:150]
        profile.telegram_linked_at = now
        profile.telegram_link_hash = ""
        profile.telegram_link_expires = None
        profile.save()
        log_event("telegram_link", profile.user, actor=profile.user, message=f"Telegram: {name or chat_id}")
    can_receive = profile.user.role in (roles.ADMIN, roles.ENGINEER)
    note = "" if can_receive else "\n\nViewers don't receive monitoring alerts; ask an Admin if you need them."
    return f"✅ Linked to NetCore as <b>{compose.esc(profile.user.name)}</b>. Alerts will arrive in this chat.{note}"


def pending_link(profile, now=None):
    now = now or timezone.now()
    return bool(profile.telegram_link_hash and profile.telegram_link_expires and profile.telegram_link_expires > now)

