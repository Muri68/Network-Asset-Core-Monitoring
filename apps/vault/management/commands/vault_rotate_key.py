"""
Re-encrypt every stored secret with the current (first) key in NETCORE_ENCRYPTION_KEYS (PLAN.md §7.3).

    1. python manage.py vault_rotate_key --generate     # prints a new key
    2. put it FIRST in NETCORE_ENCRYPTION_KEYS, keep the old one after it; restart the app and run_monitor
    3. python manage.py vault_rotate_key                # re-encrypts everything with the new key
    4. remove the old key from NETCORE_ENCRYPTION_KEYS; restart again

Either everything is re-encrypted or nothing is (one transaction). `--check` only reports.
"""

from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.alerts.models import AlertSettings
from apps.audit.context import acting_as
from apps.audit.log import log_event
from apps.core import crypto
from apps.vault.models import Credential, CredentialHistory


class Command(BaseCommand):
    help = "Re-encrypt vault passwords, notes and the Telegram bot token with the newest encryption key."

    def add_arguments(self, parser):
        parser.add_argument("--generate", action="store_true", help="Print a new key and the next steps.")
        parser.add_argument("--check", action="store_true", help="Only report which keys are in use.")

    def handle(self, *args, generate=False, check=False, **opts):
        if generate:
            key = crypto.new_key()
            self.stdout.write(f"New key (id {crypto.key_id(key)}):\n\n    {key}\n")
            self.stdout.write("Put it FIRST in NETCORE_ENCRYPTION_KEYS, keep the current key after it\n"
                              "(comma-separated), restart NetCore and run_monitor, then run:\n"
                              "    python manage.py vault_rotate_key\n"
                              "Keep an offline copy of the new key.")
            return

        current = crypto.current_key_id()
        in_use = Counter(Credential.all_objects.values_list("key_id", flat=True))
        in_use.update(CredentialHistory.objects.values_list("key_id", flat=True))
        self.stdout.write(f"Current key: {current}")
        for key, count in sorted(in_use.items()):
            self.stdout.write(f"  {key}: {count} password(s){'  ← current' if key == current else ''}")
        if check:
            return

        with acting_as("Command line (vault_rotate_key)"), transaction.atomic():
            counts = self._rotate(current)
            log_event("key_rotate", message=f"Re-encrypted with key {current}: {counts['passwords']} passwords, "
                                            f"{counts['history']} old passwords, bot token: {counts['token']}")
        self.stdout.write(self.style.SUCCESS(
            f"Done: {counts['passwords']} passwords, {counts['history']} old passwords"
            f"{' and the Telegram bot token' if counts['token'] else ''} now use key {current}. "
            "You can remove the old key(s) from NETCORE_ENCRYPTION_KEYS."))

    def _rotate(self, current):
        def rotate(token, what):
            try:
                return crypto.rotate(token)
            except crypto.InvalidToken:
                raise CommandError(f"{what} can't be decrypted with any configured key — nothing was changed. "
                                   "Is an old key missing from NETCORE_ENCRYPTION_KEYS?") from None

        counts = {"passwords": 0, "history": 0, "token": False}
        # .update() skips the audit diff per row; one key_rotate entry records the whole run.
        for c in Credential.all_objects.only("pk", "name", "secret_encrypted", "notes_encrypted"):
            Credential.all_objects.filter(pk=c.pk).update(
                secret_encrypted=rotate(c.secret_encrypted, f"The password of “{c.name}”"),
                notes_encrypted=rotate(c.notes_encrypted, f"The notes of “{c.name}”") if c.notes_encrypted else "",
                key_id=current)
            counts["passwords"] += 1
        for h in CredentialHistory.objects.select_related("credential"):
            CredentialHistory.objects.filter(pk=h.pk).update(
                secret_encrypted=rotate(h.secret_encrypted, f"An old password of “{h.credential.name}”"),
                key_id=current)
            counts["history"] += 1
        alert_settings = AlertSettings.objects.filter(pk=1).first()
        if alert_settings and alert_settings.bot_token_encrypted:
            AlertSettings.objects.filter(pk=1).update(
                bot_token_encrypted=rotate(alert_settings.bot_token_encrypted, "The Telegram bot token"))
            counts["token"] = True
        return counts
