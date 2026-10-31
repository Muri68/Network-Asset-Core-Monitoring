from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.audit.context import acting_as
from apps.inventory.models import Device, DeviceAttribute, DeviceModel, Location, Manufacturer, Rack
from apps.ui import fake_data as fd


class Command(BaseCommand):
    help = "Load the sample locations, racks and devices from the UI prototype (only into an empty inventory)."

    @transaction.atomic
    def handle(self, *args, **opts):
        if Device.objects.exists():
            raise CommandError("Devices already exist. The demo inventory only loads into an empty inventory.")
        with acting_as("Command line (load_demo_inventory)"):
            locations = {
                loc["name"]: Location.objects.get_or_create(name=loc["name"], defaults={"address": loc["address"]})[0]
                for loc in fd.LOCATIONS
            }
            server_room = locations["HQ — Server Room"]
            racks = {
                r["id"]: Rack.objects.get_or_create(location=server_room, name=r["name"],
                                                    defaults={"height_u": r["height"]})[0]
                for r in fd.RACKS
            }
            created = {}
            for d in fd.DEVICES:
                manufacturer = (Manufacturer.objects.filter(name__iexact=d["manufacturer"]).first()
                                or Manufacturer.objects.create(name=d["manufacturer"]))
                model, _ = DeviceModel.objects.get_or_create(
                    manufacturer=manufacturer, name=d["model"],
                    defaults={"device_type": d["type"], "height_u": d["height_u"]},
                )
                device = Device(
                    name=d["name"], device_type=d["type"], device_model=model, status=d["status"],
                    serial=d["serial"], asset_tag=d["asset_tag"],
                    firmware="" if d["firmware"] == "—" else d["firmware"],
                    mgmt_ip=d["mgmt_ip"], mac="" if d["mac"] == "—" else d["mac"],
                    location=locations[d["location"]], rack=racks.get(d["rack_id"]),
                    position_u=d["position_u"], height_u=d["height_u"], purchase_date=d["purchase_date"],
                    warranty_expiry=d["warranty_expiry"], owner=d["owner"], notes=d["notes"],
                )
                device.full_clean()
                device.save()
                for key, value in d["attributes"].items():
                    DeviceAttribute.objects.create(device=device, key=key, value=value)
                created[d["id"]] = device
            for d in fd.DEVICES:
                if d["parent_id"]:
                    device = created[d["id"]]
                    device.parent = created[d["parent_id"]]
                    device.save()
        self.stdout.write(self.style.SUCCESS(
            f"Loaded {len(locations)} locations, {len(racks)} racks and {len(created)} devices."
        ))
