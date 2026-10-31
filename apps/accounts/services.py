"""Account operations shared by views, the bootstrap command and tests."""

import base64
import secrets
from io import BytesIO

import qrcode
import qrcode.image.svg
from axes.utils import reset as axes_reset
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.audit.log import log_event

from . import roles
from .models import User

BACKUP_CODE_COUNT = 10
TOTP_DEVICE_NAME = "Authenticator app"
BACKUP_DEVICE_NAME = "Backup codes"

# No 0/O, 1/l/I: temporary passwords are read aloud or copied by hand.
_TEMP_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_temp_password():
    """e.g. 'k7Rm-Qx4t-Hb9w-Zp2e' — 16 random characters, well over the 12 minimum."""
    groups = ["".join(secrets.choice(_TEMP_ALPHABET) for _ in range(4)) for _ in range(4)]
    return "-".join(groups)


# --------------------------------------------------------------------------
# Users & roles
# --------------------------------------------------------------------------


class LastAdminError(Exception):
    """Raised when a change would leave no active Admin."""


def active_admin_count(exclude=None):
    qs = User.objects.filter(is_active=True, groups__name=roles.ADMIN)
    if exclude is not None:
        qs = qs.exclude(pk=exclude.pk)
    return qs.count()


def guard_last_admin(user, new_role, new_active):
    """Refuse to demote or deactivate the only remaining active Admin."""
    if user.role == roles.ADMIN and user.is_active and (new_role != roles.ADMIN or not new_active):
        if active_admin_count(exclude=user) == 0:
            raise LastAdminError("This is the only active Admin. Make someone else an Admin first.")


@transaction.atomic
def create_user(*, username, full_name, email, role, password=None):
    """Create a user with a role. Without a password, a temporary one is generated and returned."""
    temp = password is None
    password = password or generate_temp_password()
    user = User(username=username, full_name=full_name, email=email or None, must_change_password=temp)
    user.set_password(password)
    user.save()
    user.set_role(role)
    return user, (password if temp else None)


def reset_password(user):
    password = generate_temp_password()
    user.set_password(password)
    user.must_change_password = True
    user.save(update_fields=["password", "must_change_password"])
    log_event("password_reset", user, message="Temporary password issued; must be changed at next sign-in")
    return password


def reset_two_factor(user):
    TOTPDevice.objects.filter(user=user).delete()
    StaticDevice.objects.filter(user=user).delete()
    log_event("2fa_reset", user, message="Authenticator and backup codes removed; set up again at next sign-in")


def unlock_login(user):
    cleared = axes_reset(username=user.username)
    log_event("login_unlock", user)
    return cleared


def login_locked_usernames():
    """Usernames currently locked out by too many failed logins."""
    from axes.models import AccessAttempt

    attempts = AccessAttempt.objects.filter(failures_since_start__gte=settings.AXES_FAILURE_LIMIT)
    if settings.AXES_COOLOFF_TIME:
        attempts = attempts.filter(attempt_time__gte=timezone.now() - settings.AXES_COOLOFF_TIME)
    return set(attempts.values_list("username", flat=True))


def unlock_vault(user):
    profile = user.profile
    profile.reveal_failed_attempts = 0
    profile.vault_locked_at = None
    profile.save(update_fields=["reveal_failed_attempts", "vault_locked_at"])
    log_event("vault_unlock", user)


# --------------------------------------------------------------------------
# Reveal code (verified in Phase 7; set and changed here)
# --------------------------------------------------------------------------


def set_reveal_code(user, code):
    profile = user.profile
    first_time = not profile.reveal_code_hash
    profile.reveal_code_hash = make_password(code)
    profile.reveal_code_set_at = timezone.now()
    profile.save(update_fields=["reveal_code_hash", "reveal_code_set_at"])
    log_event("reveal_code_change", user, message="Reveal code created" if first_time else "Reveal code changed")


def check_reveal_code(user, code):
    return bool(user.profile.reveal_code_hash) and check_password(code, user.profile.reveal_code_hash)


# --------------------------------------------------------------------------
# Two-factor authentication
# --------------------------------------------------------------------------


def confirmed_totp(user):
    return TOTPDevice.objects.filter(user=user, confirmed=True).first()


def pending_totp(user):
    """The unconfirmed device being enrolled (created on first visit to the setup page)."""
    device = TOTPDevice.objects.filter(user=user, confirmed=False).first()
    if device is None:
        device = TOTPDevice.objects.create(user=user, name=TOTP_DEVICE_NAME, confirmed=False)
    return device


def totp_secret(device):
    """Base32 secret for typing into an authenticator app by hand."""
    return base64.b32encode(device.bin_key).decode().rstrip("=")


def totp_qr_svg(device):
    image = qrcode.make(device.config_url, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buffer = BytesIO()
    image.save(buffer)
    return buffer.getvalue().decode()


def new_backup_codes(user):
    """Replace the user's backup codes and return the new plaintext codes (shown once)."""
    device, _ = StaticDevice.objects.get_or_create(user=user, name=BACKUP_DEVICE_NAME, defaults={"confirmed": True})
    device.token_set.all().delete()
    codes = [StaticToken.random_token() for _ in range(BACKUP_CODE_COUNT)]
    StaticToken.objects.bulk_create(StaticToken(device=device, token=c) for c in codes)
    return codes


def backup_codes_left(user):
    return StaticToken.objects.filter(device__user=user, device__confirmed=True).count()
