from django.apps import AppConfig


class DcimConfig(AppConfig):
    name = "apps.dcim"
    label = "dcim"
    verbose_name = "Ports & connections"

    def ready(self):
        from apps.audit.log import track
        from apps.core.models import TrackedModel

        from .models import VLAN, Connection, ExternalEndpoint, Port

        for model in (VLAN, ExternalEndpoint, Connection):
            track(model, exclude=TrackedModel.AUDIT_EXCLUDE)
        track(Port, exclude=[*TrackedModel.AUDIT_EXCLUDE, "sort_key"])
