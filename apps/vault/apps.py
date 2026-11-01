from django.apps import AppConfig


class VaultConfig(AppConfig):
    name = "apps.vault"
    label = "vault"
    verbose_name = "Password vault"

    def ready(self):
        from apps.audit.log import track
        from apps.core.models import TrackedModel

        from .models import Credential, CredentialFolder

        # Ciphertexts are only ever shown as "••• changed"; deleting is logged explicitly (soft delete).
        track(Credential, exclude=[*TrackedModel.AUDIT_EXCLUDE, "key_id", "deleted_at", "password_changed_at"],
              mask=["secret_encrypted", "notes_encrypted"])
        track(CredentialFolder, exclude=TrackedModel.AUDIT_EXCLUDE)
