from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.audit.context import acting_as
from apps.inventory.models import Device, DeviceAttribute, DeviceModel, Location, Manufacturer, Rack
from apps.ui import fake_data as fd


class Command(BaseCommand):
    help = ("Load the sample locations, racks, devices, VLANs, ports, cables, ISPs, prefixes and IPs "
            "from the UI prototype "
            "(only into an empty inventory).")

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
            vlans, ports, cables = self._load_ports_and_cables(created)
            prefixes, ips = self._load_ipam(locations)
        self.stdout.write(self.style.SUCCESS(
            f"Loaded {len(locations)} locations, {len(racks)} racks, {len(created)} devices, "
            f"{vlans} VLANs, {ports} ports, {cables} cables, {prefixes} prefixes and {ips} IP assignments."
        ))

    def _load_ipam(self, locations):
        import re
        from decimal import Decimal

        from apps.dcim.models import VLAN, Port
        from apps.ipam.models import ISP, Circuit, IPAddress, Prefix

        isps = {}
        for i in fd.ISPS:
            isp = ISP.objects.create(name=i["name"], account_number=i["account"], support_phone=i["phone"],
                                     support_email=i["email"], account_manager=i["manager"], portal_url=i["portal"])
            isps[isp.name] = isp
            for c in i["circuits"]:
                device_name, port_name = c["termination"].split(" · ")
                cost = Decimal(re.sub(r"[^\d.]", "", c["cost"].split("/")[0]))
                Circuit.objects.create(
                    isp=isp, circuit_id=c["id"], bandwidth=c["bandwidth"],
                    circuit_type=Circuit.Type.FIBRE if "Fibre" in c["type"] else Circuit.Type.RADIO,
                    contract_end=c["contract_end"], monthly_cost=cost, currency="NGN",
                    termination_port=Port.objects.filter(device__name=device_name, name=port_name).first(),
                )
        server_room = locations["HQ — Server Room"]
        for p in fd.PREFIXES:
            prefix = Prefix(cidr=p["cidr"], kind=p["kind"], isp=isps.get(p["isp"]), gateway=p["gateway"],
                            description=p["desc"], location=server_room,
                            vlan=VLAN.objects.filter(vid=p["vlan"]).first() if p["vlan"] else None)
            if prefix.isp:
                prefix.circuit = prefix.isp.circuits.first()
            prefix.full_clean()
            prefix.save()
        count = 0
        for address, a in fd.IP_ASSIGNMENTS.items():
            nat = re.match(r"→ ([\d.]+) : (.+)", a["nat"] or "")
            ip = IPAddress(address=address, status=a["status"], service=a["service"], dns_name=a["dns"],
                           description=a["desc"], device=Device.objects.filter(name=a["device"]).first(),
                           nat_to=nat[1] if nat else None, nat_ports=nat[2] if nat else "")
            ip.full_clean()
            ip.save()
            count += 1
        return len(fd.PREFIXES), count

    def _load_ports_and_cables(self, devices_by_fake_id):
        from apps.dcim.models import VLAN, Connection, ExternalEndpoint
        from apps.dcim.services import apply_model_template, set_port_vlans

        vlans = {v["vid"]: VLAN.objects.create(vid=v["vid"], name=v["name"], description=v["desc"])
                 for v in fd.VLANS if v["vid"] != 1}
        # Port templates per model, taken from the prototype's port lists.
        port_count = 0
        for fake in fd.DEVICES:
            device = devices_by_fake_id[fake["id"]]
            model = device.device_model
            if not model.port_template:
                names = [n for n in fd._port_names(fake) if not n.endswith("(rear)")]
                model.port_template = "\n".join(names)
                model.save()
            created, _ = apply_model_template(device)
            port_count += len(created)

        by_name = {d.name: d for d in devices_by_fake_id.values()}
        cables = 0
        for c in fd.CONNECTIONS:
            a_port = by_name[c["a_device"]["name"]].ports.get(name=c["a_port"])
            connection = Connection(a_port=a_port, function=c["function"], cable_label=c["cable"], remark=c["remark"])
            if c["b_device"]:
                connection.b_port = by_name[c["b_device"]["name"]].ports.get(name=c["b_port"])
            else:
                connection.b_external, _ = ExternalEndpoint.objects.get_or_create(name=c["b_external"])
            connection.full_clean()
            connection.save()
            cables += 1
            ids = [int(v) for v in c["vlans"].split(",") if v.strip().isdigit()] if c["vlans"] != "all" else list(vlans)
            for port in filter(None, (connection.a_port, connection.b_port)):
                if c["mode"] in ("access", "trunk", "routed"):
                    port.mode = c["mode"]
                if c["mode"] == "access" and ids:
                    set_port_vlans(port, vlans.get(ids[0]), [])
                elif c["mode"] == "trunk":
                    set_port_vlans(port, None, [vlans[v] for v in ids if v in vlans])
                else:
                    port.save()
        return len(vlans), port_count, cables
