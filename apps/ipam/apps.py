from django.apps import AppConfig


class IpamConfig(AppConfig):
    name = "apps.ipam"
    label = "ipam"
    verbose_name = "IP addressing"

    def ready(self):
        from apps.audit.log import track
        from apps.core.models import TrackedModel

        from . import signals  # noqa: F401  (re-homes IPs when prefixes change)
        from .models import ISP, Circuit, IPAddress, Prefix

        for model in (ISP, Circuit):
            track(model, exclude=TrackedModel.AUDIT_EXCLUDE)
        track(Prefix, exclude=[*TrackedModel.AUDIT_EXCLUDE, "network_int", "broadcast_int", "prefixlen"])
        track(IPAddress, exclude=[*TrackedModel.AUDIT_EXCLUDE, "address_int"])
