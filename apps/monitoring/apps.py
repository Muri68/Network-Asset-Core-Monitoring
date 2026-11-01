from django.apps import AppConfig


class MonitoringConfig(AppConfig):
    name = "apps.monitoring"
    label = "monitoring"
    verbose_name = "Monitoring"

    def ready(self):
        from apps.audit.log import track
        from apps.core.models import TrackedModel

        from .models import MaintenanceWindow, Monitor

        # Live state (status, last check, …) changes every minute; only configuration is audited.
        track(Monitor, exclude=[*TrackedModel.AUDIT_EXCLUDE, *Monitor.STATE_FIELDS])
        track(MaintenanceWindow, exclude=TrackedModel.AUDIT_EXCLUDE)
