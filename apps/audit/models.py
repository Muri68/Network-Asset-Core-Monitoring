from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone

from .actions import ACTIONS, CHOICES


class AppendOnlyError(Exception):
    """The audit log can only be added to."""


class AuditLogQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnlyError("The audit log can't be changed.")

    def delete(self):
        raise AppendOnlyError("The audit log can't be deleted.")


class AuditLog(models.Model):
    """
    One thing that happened (PLAN.md §6.8). Rows are never changed or deleted:
    besides the guards here, database triggers (migration 0002) refuse it.
    """

    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    # No DB constraint and DO_NOTHING: deleting a user must never touch (UPDATE) audit rows.
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.DO_NOTHING,
        db_constraint=False, related_name="+",
    )
    actor_repr = models.CharField(max_length=150)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    action = models.CharField(max_length=40, choices=CHOICES, db_index=True)
    object_type = models.ForeignKey(
        ContentType, null=True, blank=True, on_delete=models.DO_NOTHING, db_constraint=False, related_name="+"
    )
    object_id = models.CharField(max_length=64, blank=True)
    object_repr = models.CharField(max_length=255, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    message = models.CharField(max_length=500, blank=True)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        ordering = ["-timestamp", "-id"]
        indexes = [models.Index(fields=["object_type", "object_id"]), models.Index(fields=["actor", "timestamp"])]

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M:%S} {self.actor_repr} {self.action} {self.object_repr}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise AppendOnlyError("The audit log can't be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError("The audit log can't be deleted.")

    @property
    def label(self):
        return ACTIONS.get(self.action, (self.action, "muted"))[0]

    @property
    def tone(self):
        return ACTIONS.get(self.action, (self.action, "muted"))[1]

    @property
    def object_label(self):
        """e.g. 'User · Grace Adeyemi'."""
        kind = self.object_type.model_class()._meta.verbose_name.capitalize() if self.object_type_id else ""
        return " · ".join(p for p in (kind, self.object_repr) if p)
