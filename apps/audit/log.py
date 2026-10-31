"""
Writing audit entries.

    log_event("password_reset", obj=user)                 # explicit events
    track(Device, exclude=["updated_at"], mask=["secret"]) # automatic create/update/delete

Secrets are never written: masked fields are recorded only as "••• changed".
"""

import datetime
import decimal
import uuid

from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_delete, post_save, pre_save

from .context import current_request, default_actor, request_details
from .models import AuditLog

MASK = "••• changed"


def _json_value(value):
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, datetime.datetime | datetime.date | datetime.time | decimal.Decimal | uuid.UUID):
        return str(value)
    if isinstance(value, list | tuple):
        return [_json_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    return str(value)


def log_event(action, obj=None, *, actor=None, actor_repr=None, changes=None, message="", request=None):
    """Record one action. Actor, IP and browser default to the current request's."""
    request = request or current_request()
    if actor is None and actor_repr is None:
        actor, actor_repr = default_actor()
    elif actor is not None and actor_repr is None:
        actor_repr = getattr(actor, "name", str(actor))
    entry = AuditLog(
        actor=actor if getattr(actor, "pk", None) else None,
        actor_repr=(actor_repr or "System")[:150],
        action=action,
        changes=_json_value(changes or {}),
        message=message[:500],
        **request_details(request),
    )
    if obj is not None:
        entry.object_type = ContentType.objects.get_for_model(obj, for_concrete_model=False)
        entry.object_id = str(obj.pk)
        entry.object_repr = str(obj)[:255]
    entry.save()
    return entry


# --------------------------------------------------------------------------
# Automatic tracking of model changes
# --------------------------------------------------------------------------

_tracked = {}


class _Config:
    def __init__(self, model, exclude, mask):
        self.model = model
        self.mask = set(mask)
        self.fields = [
            f for f in model._meta.concrete_fields
            if f.name not in set(exclude) and not f.primary_key
        ]

    def snapshot(self, instance):
        values = {}
        for f in self.fields:
            value = f.value_from_object(instance)
            values[f.name] = MASK if (f.name in self.mask and value) else _json_value(value)
        return values

    def raw(self, instance):
        return {f.name: f.value_from_object(instance) for f in self.fields}


def _pre_save(sender, instance, raw=False, **kwargs):
    config = _tracked.get(sender)
    if config is None or raw or instance.pk is None:
        return
    old = sender._default_manager.filter(pk=instance.pk).first()
    instance._audit_old = config.raw(old) if old is not None else None


def _post_save(sender, instance, created, raw=False, **kwargs):
    config = _tracked.get(sender)
    if config is None or raw:
        return
    if created or getattr(instance, "_audit_old", None) is None:
        changes = {k: [None, v] for k, v in config.snapshot(instance).items() if v not in (None, "", [], {})}
        log_event("create", instance, changes=changes)
        return
    old, new = instance._audit_old, config.raw(instance)
    changes = {}
    for name, new_value in new.items():
        if old.get(name) != new_value:
            if name in config.mask:
                changes[name] = [MASK, MASK]
            else:
                changes[name] = [_json_value(old.get(name)), _json_value(new_value)]
    instance._audit_old = None
    if changes:
        log_event("update", instance, changes=changes)


def _post_delete(sender, instance, **kwargs):
    if sender in _tracked:
        log_event("delete", instance, changes={k: [v, None] for k, v in _tracked[sender].snapshot(instance).items()
                                                if v not in (None, "", [], {})})


def track(model, *, exclude=(), mask=()):
    """Log every create / update / delete of `model`, with field-level changes."""
    _tracked[model] = _Config(model, exclude, mask)
    uid = f"audit-{model._meta.label}"
    pre_save.connect(_pre_save, sender=model, dispatch_uid=f"{uid}-pre")
    post_save.connect(_post_save, sender=model, dispatch_uid=f"{uid}-post")
    post_delete.connect(_post_delete, sender=model, dispatch_uid=f"{uid}-del")


def untrack(model):
    """Stop tracking `model` (used by tests)."""
    _tracked.pop(model, None)
    uid = f"audit-{model._meta.label}"
    pre_save.disconnect(sender=model, dispatch_uid=f"{uid}-pre")
    post_save.disconnect(sender=model, dispatch_uid=f"{uid}-post")
    post_delete.disconnect(sender=model, dispatch_uid=f"{uid}-del")
