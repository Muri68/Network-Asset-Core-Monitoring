"""
Password vault (PLAN.md §6.7, §7.3). Passwords and notes are stored only as ciphertext
(apps/core/crypto.py); the plain text exists in memory just long enough to show it to someone who entered
their reveal code. Every reveal, copy and wrong code is a RevealLog row, which the database refuses to
change or delete.
"""

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from apps.core import crypto
from apps.core.models import TrackedModel
from apps.inventory.models import Device

ROTATE_AFTER_DAYS = 90


class CredentialFolder(TrackedModel):
    name = models.CharField(max_length=80, unique=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class LiveCredentials(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class Credential(TrackedModel):
    class Protocol(models.TextChoices):
        SSH = "ssh", "SSH"
        HTTPS = "https", "HTTPS / web"
        WINBOX = "winbox", "Winbox"
        TELNET = "telnet", "Telnet"
        RDP = "rdp", "RDP"
        OTHER = "other", "Other"

    folder = models.ForeignKey(CredentialFolder, on_delete=models.PROTECT, related_name="credentials")
    name = models.CharField(max_length=120, help_text="e.g. Core switch 01 — admin")
    device = models.ForeignKey(Device, on_delete=models.SET_NULL, null=True, blank=True, related_name="credentials")
    username = models.CharField(max_length=150, blank=True)
    host = models.CharField("host or URL", max_length=255, blank=True)
    protocol = models.CharField(max_length=10, choices=Protocol.choices, default=Protocol.SSH)
    secret_encrypted = models.TextField(editable=False)
    notes_encrypted = models.TextField(blank=True, editable=False)
    key_id = models.CharField(max_length=16, editable=False, help_text="Key the password is encrypted with.")
    password_changed_at = models.DateTimeField(default=timezone.now, editable=False)
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)

    objects = LiveCredentials()
    all_objects = models.Manager()

    class Meta:
        ordering = ["name"]
        base_manager_name = "all_objects"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("vault:credential", args=[self.pk])

    # Secrets: set through these, read only via reveal (views.py).

    @property
    def secret(self):
        return crypto.decrypt(self.secret_encrypted)

    @property
    def notes(self):
        return crypto.decrypt(self.notes_encrypted)

    def set_notes(self, notes):
        self.notes_encrypted = crypto.encrypt(notes) if notes else ""

    def set_secret(self, secret, by=None):
        """Store a new password; the previous one goes to history."""
        if self.pk and self.secret_encrypted:
            CredentialHistory.objects.create(credential=self, secret_encrypted=self.secret_encrypted,
                                             key_id=self.key_id, replaced_by=by)
        self.secret_encrypted = crypto.encrypt(secret)
        self.key_id = crypto.current_key_id()
        self.password_changed_at = timezone.now()

    @property
    def age_days(self):
        return (timezone.now() - self.password_changed_at).days

    @property
    def needs_rotation(self):
        return self.age_days > ROTATE_AFTER_DAYS

    @property
    def is_url(self):
        return self.host.startswith(("http://", "https://"))


class CredentialHistory(models.Model):
    credential = models.ForeignKey(Credential, on_delete=models.CASCADE, related_name="history")
    secret_encrypted = models.TextField()
    key_id = models.CharField(max_length=16)
    replaced_at = models.DateTimeField(default=timezone.now)
    replaced_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="+")

    class Meta:
        ordering = ["-replaced_at"]
        verbose_name_plural = "credential history"

    def __str__(self):
        return f"{self.credential} (replaced {self.replaced_at:%Y-%m-%d})"

    @property
    def secret(self):
        return crypto.decrypt(self.secret_encrypted)


class RevealLog(models.Model):
    """Append-only (database triggers in migration 0002)."""

    class Action(models.TextChoices):
        REVEAL = "reveal", "Revealed"
        COPY = "copy", "Copied"
        FAILED = "reveal_failed", "Wrong reveal code"
        LOCKED = "locked", "Vault locked"
        HISTORY = "history_reveal", "Revealed an old password"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reveal_logs")
    credential = models.ForeignKey(Credential, on_delete=models.PROTECT, related_name="reveal_logs")
    action = models.CharField(max_length=16, choices=Action.choices)
    at = models.DateTimeField(default=timezone.now, db_index=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-at"]
        indexes = [models.Index(fields=["credential", "-at"]), models.Index(fields=["user", "-at"])]

    def __str__(self):
        return f"{self.user} {self.get_action_display().lower()} {self.credential}"

    @property
    def tone(self):
        return {"reveal": "warn", "copy": "warn", "history_reveal": "warn"}.get(self.action, "down")
