from django.conf import settings
from django.db import models

from apps.audit.context import default_actor


class TrackedModel(models.Model):
    """created/updated timestamps and users (PLAN.md §6), filled in from the current request."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, editable=False, on_delete=models.SET_NULL, related_name="+"
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, editable=False, on_delete=models.SET_NULL, related_name="+"
    )

    # Bookkeeping fields the audit log doesn't need to repeat in every diff.
    AUDIT_EXCLUDE = ["created_at", "updated_at", "created_by", "updated_by"]

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        user, _label = default_actor()
        if user is not None:
            if self._state.adding and self.created_by_id is None:
                self.created_by = user
            self.updated_by = user
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = {*kwargs["update_fields"], "updated_by", "updated_at"}
        super().save(*args, **kwargs)
