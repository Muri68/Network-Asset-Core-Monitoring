"""Who is acting right now, so audit entries know their actor, IP and browser."""

from contextlib import contextmanager
from contextvars import ContextVar

_request = ContextVar("audit_request", default=None)
_actor_label = ContextVar("audit_actor_label", default=None)


class AuditContextMiddleware:
    """Remember the current request for audit entries written during it."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _request.set(request)
        try:
            return self.get_response(request)
        finally:
            _request.reset(token)


def current_request():
    return _request.get()


@contextmanager
def acting_as(label):
    """Label entries made outside a web request, e.g. acting_as('Command line')."""
    token = _actor_label.set(label)
    try:
        yield
    finally:
        _actor_label.reset(token)


def client_ip(request):
    # Behind Nginx, apps.core.middleware.ProxyClientIPMiddleware has already put the real address here.
    return request.META.get("REMOTE_ADDR") or None


def request_details(request):
    if request is None:
        return {"ip": None, "user_agent": ""}
    return {"ip": client_ip(request), "user_agent": request.META.get("HTTP_USER_AGENT", "")[:300]}


def default_actor():
    """(user, label) for the current context."""
    request = current_request()
    user = getattr(request, "user", None) if request is not None else None
    if user is not None and user.is_authenticated:
        return user, user.name
    return None, _actor_label.get() or "System"
