from django.apps import AppConfig


class AuditConfig(AppConfig):
    name = "apps.audit"
    label = "audit"
    verbose_name = "Audit log"

    def ready(self):
        from . import receivers  # noqa: F401  (connects auth signal handlers)
