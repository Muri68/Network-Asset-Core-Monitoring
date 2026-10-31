from django.apps import AppConfig


class InventoryConfig(AppConfig):
    name = "apps.inventory"
    label = "inventory"
    verbose_name = "Inventory"

    def ready(self):
        from apps.audit.log import track
        from apps.core.models import TrackedModel

        from .models import Device, DeviceAttribute, DeviceModel, Location, Manufacturer, Rack

        for model in (Location, Rack, Manufacturer, DeviceModel, Device, DeviceAttribute):
            track(model, exclude=TrackedModel.AUDIT_EXCLUDE)
