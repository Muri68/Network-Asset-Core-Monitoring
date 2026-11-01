"""
The reveal rules (PLAN.md §7.3, steps 3–5), separate from the views so every rule is testable:

- only vault roles; a locked vault or a missing reveal code reveals nothing;
- right code → the secret, failures reset, `reveal` logged (RevealLog + audit);
- wrong code → `reveal_failed` logged; the 5th wrong code in a row locks the vault and tells the Admins
  on Telegram; only an Admin can unlock it (Users & roles).
"""

import datetime
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.accounts import roles
from apps.accounts.models import Profile
from apps.accounts.services import check_reveal_code
from apps.audit.context import request_details
from apps.audit.log import log_event

from .models import RevealLog

MAX_ATTEMPTS = 5
COPY_WINDOW_SECONDS = 120  # a copy is only accepted shortly after a reveal of the same password


@dataclass
class Outcome:
    status: str  # "ok", "wrong", "locked", "no_code", "forbidden"
    secret: str = ""
    notes: str = ""
    remaining: int = 0


def _log(user, credential, action, request):
    return RevealLog.objects.create(user=user, credential=credential, action=action, **request_details(request))


def reveal(user, credential, code, request=None, history=None):
    """Check `code` and return the password of `credential` (or of the `history` entry, Admins only)."""
    if not roles.can(user, "vault") or (history is not None and not roles.can(user, "vault_logs_all")):
        return Outcome("forbidden")
    with transaction.atomic():
        profile = Profile.objects.select_for_update().get(user=user)
        if profile.vault_locked:
            return Outcome("locked")
        if not profile.has_reveal_code:
            return Outcome("no_code")
        if check_reveal_code(user, code or ""):
            if profile.reveal_failed_attempts:
                profile.reveal_failed_attempts = 0
                profile.save(update_fields=["reveal_failed_attempts"])
            if history is not None:
                _log(user, credential, RevealLog.Action.HISTORY, request)
                log_event("history_reveal", credential, actor=user, request=request,
                          message=f"Password replaced {timezone.localtime(history.replaced_at):%d %b %Y}")
                return Outcome("ok", secret=history.secret)
            _log(user, credential, RevealLog.Action.REVEAL, request)
            log_event("reveal", credential, actor=user, request=request)
            return Outcome("ok", secret=credential.secret, notes=credential.notes)

        profile.reveal_failed_attempts += 1
        _log(user, credential, RevealLog.Action.FAILED, request)
        log_event("reveal_failed", credential, actor=user, request=request,
                  message=f"Wrong reveal code ({profile.reveal_failed_attempts} of {MAX_ATTEMPTS})")
        if profile.reveal_failed_attempts >= MAX_ATTEMPTS:
            profile.vault_locked_at = timezone.now()
            profile.save(update_fields=["reveal_failed_attempts", "vault_locked_at"])
            _log(user, credential, RevealLog.Action.LOCKED, request)
            log_event("vault_locked", user, actor=user, request=request,
                      message=f"{MAX_ATTEMPTS} wrong reveal codes (last on {credential})")
            _tell_admins(user, credential, profile.vault_locked_at, request)
            return Outcome("locked")
        profile.save(update_fields=["reveal_failed_attempts"])
        return Outcome("wrong", remaining=MAX_ATTEMPTS - profile.reveal_failed_attempts)


def _tell_admins(user, credential, locked_at, request):
    from apps.alerts import compose
    from apps.alerts.outbox import notify_admins

    ip = request_details(request)["ip"] or "unknown IP"
    text = (f"🔒 <b>Vault locked: {compose.esc(user.name)}</b>\n"
            f"{MAX_ATTEMPTS} wrong reveal codes, the last for “{compose.esc(credential.name)}” from {ip}.\n"
            f"An Admin must unlock it in NetCore → Users &amp; roles.")
    notify_admins(text, subject=f"vault-lock-{user.pk}-{int(locked_at.timestamp())}")


def record_copy(user, credential, request=None):
    """Log a copy; only accepted right after this user revealed this password. Returns True if logged."""
    since = timezone.now() - datetime.timedelta(seconds=COPY_WINDOW_SECONDS)
    recent = RevealLog.objects.filter(user=user, credential=credential, at__gte=since,
                                      action__in=[RevealLog.Action.REVEAL, RevealLog.Action.HISTORY]).exists()
    if not recent:
        return False
    _log(user, credential, RevealLog.Action.COPY, request)
    log_event("copy", credential, actor=user, request=request)
    return True
