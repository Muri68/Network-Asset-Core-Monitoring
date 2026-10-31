"""
Multi-step sign-in.

After a correct password the user is *not* logged in yet. The session holds a
short-lived "pending" record while they finish the remaining steps:

    password → (new password if temporary) → (2FA setup | 2FA code) → logged in

Only the 2FA step calls django.contrib.auth.login(), so no page behind the
login is reachable with a password alone.
"""

import time

from django.conf import settings
from django.contrib.auth import login
from django.shortcuts import resolve_url
from django.urls import reverse
from django_otp import login as otp_login

from . import services
from .models import User

PENDING = "auth_pending"
BACKEND = "apps.accounts.backends.UsernameOrEmailBackend"


def start(request, user, next_url=""):
    request.session.cycle_key()
    request.session[PENDING] = {"uid": user.pk, "at": time.time(), "fails": 0, "next": next_url}


def pending_user(request):
    data = request.session.get(PENDING)
    if not data:
        return None
    if time.time() - data["at"] > settings.NETCORE_LOGIN_STEP_TIMEOUT.total_seconds():
        cancel(request)
        return None
    return User.objects.filter(pk=data["uid"], is_active=True).first()


def cancel(request):
    request.session.pop(PENDING, None)


def record_2fa_failure(request):
    """Count a wrong code; returns True when the attempt limit is reached (sign-in is cancelled)."""
    data = request.session.get(PENDING)
    if not data:
        return True
    data["fails"] += 1
    request.session[PENDING] = data
    if data["fails"] >= settings.NETCORE_MAX_2FA_FAILURES:
        cancel(request)
        return True
    return False


def next_step_url(user):
    if user.must_change_password:
        return reverse("accounts:login_password")
    if services.confirmed_totp(user) is None:
        return reverse("accounts:login_2fa_setup")
    return reverse("accounts:login_2fa")


def complete(request, user, device):
    """Log the user in after the second factor; returns where to go next."""
    next_url = request.session.get(PENDING, {}).get("next") or resolve_url(settings.LOGIN_REDIRECT_URL)
    cancel(request)
    login(request, user, backend=BACKEND)
    otp_login(request, device)
    request.session["last_activity"] = time.time()
    return next_url
