"""Sign-in events from Django and django-axes, plus model tracking registrations."""

from axes.signals import user_locked_out
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .log import log_event, track

User = get_user_model()

# User records: passwords, sign-in timestamps and the temp-password flag have their own
# explicit events (or none), so they're left out of the field diff.
track(User, exclude=["password", "last_login", "date_joined", "must_change_password",
                   "is_staff", "is_superuser", "first_name", "last_name"])


@receiver(user_logged_in, dispatch_uid="audit-login")
def on_login(sender, request, user, **kwargs):
    log_event("login", user, actor=user, request=request)


@receiver(user_logged_out, dispatch_uid="audit-logout")
def on_logout(sender, request, user, **kwargs):
    if user is None:
        return
    reason = getattr(request, "logout_reason", "")
    log_event("logout", user, actor=user, request=request, message=reason)


@receiver(user_login_failed, dispatch_uid="audit-login-failed")
def on_login_failed(sender, credentials, request=None, **kwargs):
    username = str(credentials.get("username", ""))[:150]
    if request is not None and getattr(request, "axes_locked_out", False):
        return  # recorded once by on_locked_out instead
    user = User.objects.filter(username__iexact=username).first() if username else None
    log_event("login_failed", user, actor_repr=f"Unknown ({username})" if username else "Unknown",
              request=request, message="Wrong username or password")


@receiver(user_locked_out, dispatch_uid="audit-locked-out")
def on_locked_out(sender, request, username, ip_address, **kwargs):
    user = User.objects.filter(username__iexact=username).first() if username else None
    # Sent for the attempt that triggers the lock and for every attempt while it lasts.
    log_event("login_locked", user, actor_repr=f"Unknown ({username})", request=request,
              message="Too many wrong passwords — sign-in locked (30 min or until an Admin unlocks it)")
