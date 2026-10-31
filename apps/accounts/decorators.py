from functools import wraps

from django.core.exceptions import PermissionDenied

from .roles import CAPABILITIES, can


def capability_required(capability):
    """Allow the view only for roles that have `capability` (see roles.CAPABILITIES)."""
    if capability not in CAPABILITIES:
        raise ValueError(f"Unknown capability: {capability}")

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not can(request.user, capability):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        wrapped.required_capability = capability
        return wrapped

    return decorator
