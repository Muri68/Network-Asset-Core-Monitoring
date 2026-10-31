import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import urlencode

from .roles import can

# Requests sent with this header (e.g. dashboard polling) don't count as activity.
BACKGROUND_HEADER = "HTTP_X_BACKGROUND"


def _to_login(request, message=None):
    if message:
        messages.warning(request, message)
    url = f"{reverse('accounts:login')}?{urlencode({'next': request.get_full_path()})}"
    if request.headers.get("HX-Request"):
        response = HttpResponse(status=204)
        response["HX-Redirect"] = url
        return response
    return redirect(url)


class SessionSecurityMiddleware:
    """
    For signed-in users:
    - a session without a verified second factor is signed out (defence in depth);
    - after NETCORE_IDLE_TIMEOUT without real activity the session is signed out;
    - Admins and Engineers must set a vault reveal code before using the app.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated:
            if not user.is_verified():
                logout(request)
                return _to_login(request)

            now = time.time()
            last = request.session.get("last_activity", now)
            if now - last > settings.NETCORE_IDLE_TIMEOUT.total_seconds():
                logout(request)
                minutes = int(settings.NETCORE_IDLE_TIMEOUT.total_seconds() // 60)
                return _to_login(request, f"You were signed out after {minutes} minutes of inactivity.")
            if not request.META.get(BACKGROUND_HEADER):
                request.session["last_activity"] = now

            setup_url = reverse("accounts:reveal_code_setup")
            allowed = (setup_url, reverse("accounts:logout"), reverse("accounts:backup_codes"))
            if can(user, "vault") and not user.profile.has_reveal_code and request.path not in allowed:
                return redirect(setup_url)

        return self.get_response(request)
