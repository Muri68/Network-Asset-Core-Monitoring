from .roles import capabilities_for


def access(request):
    """`can.<capability>` in templates, e.g. {% if can.edit %}…{% endif %}."""
    user = getattr(request, "user", None)
    return {"can": capabilities_for(user) if user is not None else {}}
