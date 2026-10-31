from django.contrib.auth.models import AbstractUser, Group
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.functional import cached_property

from . import roles


class User(AbstractUser):
    email = models.EmailField("email address", unique=True, blank=True, null=True)
    full_name = models.CharField(max_length=150, blank=True)
    must_change_password = models.BooleanField(
        default=False, help_text="Set for temporary passwords; the user must choose a new one at next login."
    )

    class Meta:
        ordering = ["full_name", "username"]

    def __str__(self):
        return self.name

    @property
    def name(self):
        return self.full_name or self.username

    @cached_property
    def role(self):
        names = {g.name for g in self.groups.all()}
        return next((r for r in roles.ROLES if r in names), None)

    def set_role(self, role):
        if role not in roles.ROLES:
            raise ValueError(f"Unknown role: {role}")
        old = self.role
        self.groups.set([Group.objects.get(name=role)])
        self.__dict__.pop("role", None)
        if old != role:
            from apps.audit.log import log_event

            log_event("user_role_change", self, changes={"role": [old, role]})

    def save(self, *args, **kwargs):
        # Store blank email as NULL so several users may leave it empty.
        if not self.email:
            self.email = None
        super().save(*args, **kwargs)


class Profile(models.Model):
    """Per-user security settings (PLAN.md §6.1)."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    reveal_code_hash = models.CharField(max_length=256, blank=True)
    reveal_code_set_at = models.DateTimeField(null=True, blank=True)
    reveal_failed_attempts = models.PositiveSmallIntegerField(default=0)
    vault_locked_at = models.DateTimeField(null=True, blank=True)
    telegram_chat_id = models.CharField(max_length=32, unique=True, null=True, blank=True)
    alerts_muted = models.BooleanField(default=False)

    def __str__(self):
        return f"Profile of {self.user}"

    @property
    def has_reveal_code(self):
        return bool(self.reveal_code_hash)

    @property
    def vault_locked(self):
        return self.vault_locked_at is not None


@receiver(post_save, sender=User)
def create_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.get_or_create(user=instance)
