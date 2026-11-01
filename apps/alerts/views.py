import uuid

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.accounts.decorators import capability_required
from apps.audit.log import log_event
from apps.monitoring.models import MonitorHeartbeat

from . import compose, outbox, telegram
from .forms import AlertSettingsForm
from .models import AlertSettings, Notification

LINK_SESSION_KEY = "telegram_link_token"


def send_test(user):
    """Send a test alert to `user` right now (not via the outbox, so the result shows at once)."""
    alert_settings = AlertSettings.load()
    if not alert_settings.configured:
        raise telegram.TelegramError("No Telegram bot is set up yet.")
    chat_id = user.profile.telegram_chat_id
    if not chat_id:
        raise telegram.TelegramError("Link your Telegram on your profile page first.")
    note = Notification.objects.create(user=user, event=Notification.Event.TEST, text=compose.test(user),
                                       dedupe_key=f"test:{uuid.uuid4().hex}")
    try:
        telegram.send(alert_settings.bot_token, chat_id, note.text)
    except telegram.TelegramError as e:
        note.status = Notification.State.FAILED
        note.error = str(e)[:255]
        note.attempts = 1
        note.save(update_fields=["status", "error", "attempts"])
        raise
    outbox.mark_sent(note)
    log_event("alert_test", user, actor=user)


def _test_and_report(request):
    try:
        send_test(request.user)
    except telegram.TelegramError as e:
        messages.error(request, f"Test alert not sent: {e}")
    else:
        messages.success(request, "Test alert sent — check Telegram.")


# --------------------------------------------------------------------------
# Settings → Telegram alerts (Admin)
# --------------------------------------------------------------------------


def settings_context(form=None):
    alert_settings = AlertSettings.load()
    return {
        "alert_settings": alert_settings,
        "alert_form": form or AlertSettingsForm(instance=alert_settings),
        "recent_alerts": Notification.objects.select_related("user", "monitor")[:15],
        "alert_counts": {s: Notification.objects.filter(status=s).count()
                         for s in (Notification.State.PENDING, Notification.State.FAILED)},
        "linked_recipients": outbox.recipients(),
    }


@capability_required("settings")
@require_POST
def settings_save(request):
    alert_settings = AlertSettings.load()
    before = {"enabled": alert_settings.enabled, "reminder_minutes": alert_settings.reminder_minutes,
              "ssl_warnings": alert_settings.ssl_warnings, "bot": alert_settings.bot_username}
    form = AlertSettingsForm(request.POST, instance=alert_settings)
    if not form.is_valid():
        from apps.ui.views import settings_page

        return settings_page(request, alert_form=form, tab="alerts", status=400)
    alert_settings = form.save(commit=False)
    token = form.cleaned_data["bot_token"]
    if token:
        if before["bot"] and form.bot_username != before["bot"]:
            removed = outbox.unlink_everyone(request.user)
            if removed:
                messages.warning(request, f"New bot: {removed} Telegram link(s) removed — everyone links again.")
        alert_settings.bot_token = token
        alert_settings.bot_username = form.bot_username
        alert_settings.last_update_id = 0
    alert_settings.save()
    after = {"enabled": alert_settings.enabled, "reminder_minutes": alert_settings.reminder_minutes,
             "ssl_warnings": alert_settings.ssl_warnings, "bot": alert_settings.bot_username}
    changes = {k: [before[k], after[k]] for k in before if before[k] != after[k]}
    if token:
        changes["bot_token"] = ["(hidden)", "(replaced)"]
    if changes:
        log_event("settings_change", alert_settings, changes=changes, message="Telegram alerts")
    messages.success(request, "Telegram alert settings saved.")
    return redirect(reverse("ui:settings") + "?tab=alerts")


@capability_required("settings")
@require_POST
def settings_test(request):
    _test_and_report(request)
    return redirect(reverse("ui:settings") + "?tab=alerts")


# --------------------------------------------------------------------------
# Profile → Telegram (every user)
# --------------------------------------------------------------------------


def profile_context(request):
    profile = request.user.profile
    alert_settings = AlertSettings.load()
    token = request.session.get(LINK_SESSION_KEY)
    waiting = bool(token) and outbox.pending_link(profile) and not profile.telegram_linked
    if not waiting:
        request.session.pop(LINK_SESSION_KEY, None)
    return {
        "tg_settings": alert_settings,
        "tg_link": f"https://t.me/{alert_settings.bot_username}?start={token}" if waiting else "",
        "tg_expires": profile.telegram_link_expires if waiting else None,
        "tg_checker_alive": getattr(MonitorHeartbeat.current(), "alive", False),
        "tg_recent": Notification.objects.filter(user=request.user).select_related("monitor")[:5],
    }


def _card(request):
    from apps.accounts.views import _profile_context

    ctx = _profile_context(request)
    return render(request, "accounts/partials/telegram_card.html", ctx)


def _back_to_card():
    return redirect(reverse("accounts:profile") + "#telegram")


@require_POST
def link(request):
    if not AlertSettings.load().configured:
        messages.error(request, "No Telegram bot is set up yet — ask an Admin.")
        return _back_to_card()
    token, _expires = outbox.start_link(request.user)
    request.session[LINK_SESSION_KEY] = token
    return _back_to_card()


@never_cache
def card(request):
    """The Telegram card on its own; the profile page polls it while waiting for the user to press Start."""
    return _card(request)


@require_POST
def unlink(request):
    outbox.unlink(request.user)
    messages.success(request, "Telegram unlinked. You won't get alerts until you link it again.")
    return _back_to_card()


@require_POST
def mute(request):
    profile = request.user.profile
    profile.alerts_muted = not profile.alerts_muted
    profile.save(update_fields=["alerts_muted"])
    log_event("alerts_mute" if profile.alerts_muted else "alerts_unmute", request.user, actor=request.user)
    messages.success(request, "Alerts muted for you." if profile.alerts_muted else "Alerts unmuted.")
    return _back_to_card()


@require_POST
def test(request):
    _test_and_report(request)
    return _back_to_card()

