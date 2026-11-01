"""
Telegram alerts (PLAN.md §6.6, §7.2): the bot settings and the notification outbox.

Every message is a Notification row first. The worker in `run_monitor` sends pending rows and retries
with back-off, so alerts survive an internet outage. `dedupe_key` is unique: the same alert can't be
queued twice for the same person.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core import crypto
from apps.monitoring.models import Incident, Monitor

REMINDER_CHOICES = [(0, "Never"), (30, "Every 30 minutes"), (60, "Every 60 minutes"), (120, "Every 2 hours"),
                    (240, "Every 4 hours")]


class AlertSettings(models.Model):
    """Single row (pk=1). Changes are audited by the settings view, never with the token itself."""

    enabled = models.BooleanField("send Telegram alerts", default=True)
    bot_token_encrypted = models.TextField(blank=True)
    bot_username = models.CharField(max_length=64, blank=True)
    reminder_minutes = models.PositiveSmallIntegerField("reminder while down", choices=REMINDER_CHOICES, default=60)
    ssl_warnings = models.BooleanField("daily SSL expiry warnings", default=True)
    last_update_id = models.BigIntegerField(default=0, help_text="Telegram getUpdates offset.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "alert settings"
        verbose_name_plural = "alert settings"

    def __str__(self):
        return "Telegram alert settings"

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]

    @property
    def bot_token(self):
        return crypto.decrypt(self.bot_token_encrypted)

    @bot_token.setter
    def bot_token(self, value):
        self.bot_token_encrypted = crypto.encrypt(value) if value else ""

    @property
    def configured(self):
        return bool(self.bot_token_encrypted and self.bot_username)

    @property
    def active(self):
        return self.enabled and self.configured

    @property
    def token_hint(self):
        """The bot number and last four characters, e.g. 7123456789:••••AbCd."""
        token = self.bot_token
        if ":" not in token:
            return ""
        bot_id, secret = token.split(":", 1)
        return f"{bot_id}:••••{secret[-4:]}"


class Notification(models.Model):
    class Event(models.TextChoices):
        DOWN = "down", "Down"
        REMINDER = "reminder", "Still down"
        RECOVERED = "recovered", "Back up"
        SSL = "ssl_expiring", "SSL expiring"
        NOTICE = "notice", "Notice"
        TEST = "test", "Test"

    class State(models.TextChoices):
        PENDING = "pending", "Waiting to send"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Not needed"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, null=True, blank=True,
                                 related_name="notifications")
    monitor = models.ForeignKey(Monitor, on_delete=models.CASCADE, null=True, blank=True,
                                related_name="notifications")
    event = models.CharField(max_length=16, choices=Event.choices)
    sequence = models.PositiveIntegerField(default=0, help_text="Reminder number, or the day for SSL warnings.")
    dedupe_key = models.CharField(max_length=120, unique=True)
    text = models.TextField()
    status = models.CharField(max_length=10, choices=State.choices, default=State.PENDING, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "next_attempt_at"])]

    def __str__(self):
        return f"{self.get_event_display()} → {self.user}"

    @property
    def tone(self):
        return {"sent": "up", "pending": "warn", "failed": "down"}.get(self.status, "muted")
