"""
What can be imported and exported, and how (PLAN.md §7.6).

Each resource lists its spreadsheet columns, finds the existing record a row refers to by a natural key
(device name, device + port, IP address…), and turns the row into data for the *same form* the web pages
use — so an imported row is validated exactly like one typed in (rack overlaps, duplicate IPs, ports
already connected, IPs outside their prefix…).

Choices accept the value or the label ("access_point" or "Access point"); exports use labels.
"""

import datetime
import re
from dataclasses import dataclass

from django.db.models import Q

from apps.dcim.forms import ConnectionForm, PortForm, VLANForm
from apps.dcim.models import VLAN, Connection, Port
from apps.inventory.forms import DeviceForm, LocationForm, RackForm
from apps.inventory.models import Device, Location, Rack
from apps.ipam.forms import IPAddressForm, PrefixForm
from apps.ipam.models import ISP, Circuit, IPAddress, Prefix, parse_network
from apps.monitoring.forms import MonitorForm
from apps.monitoring.models import Monitor


class RowError(Exception):
    """This row can't be imported; the message says why."""


class Missing(RowError):
    """A referenced record doesn't exist (yet): it may be created further down the same sheet."""


@dataclass
class Col:
    header: str
    help: str = ""
    example: str = ""
    required: bool = False
    export_only: bool = False  # shown in exports for reference, ignored when importing


YES = {"yes", "y", "true", "1", "on", "x", "✓"}
NO = {"no", "n", "false", "0", "off", ""}


def choice(model, field, text, column):
    if not text:
        return ""
    options, labels = {}, []
    for value, label in model._meta.get_field(field).choices:
        if value == "":
            continue
        labels.append(str(label))
        # "Ping (ICMP)" also matches "Ping", "HTTP(S) keyword" also "HTTP keyword", "Copper (RJ45)" also "Copper".
        short = re.sub(r"\s*\([^)]*\)", "", str(label)).strip()
        for key in (str(value), str(label), str(value).replace("_", " "), short):
            options[key.lower()] = value
    try:
        return options[text.strip().lower()]
    except KeyError:
        raise RowError(f"{column}: “{text}” isn't one of: {', '.join(labels)}.") from None


def boolean(text, column):
    t = text.strip().lower()
    if t in YES:
        return True
    if t in NO:
        return False
    raise RowError(f"{column}: use yes or no, not “{text}”.")


DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y"]


def date(text, column):
    if not text:
        return ""
    for fmt in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(text.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    raise RowError(f"{column}: “{text}” isn't a date. Use 2026-11-30 or 30/11/2026.")


def _label(model, field, value):
    return str(dict(model._meta.get_field(field).choices).get(value, value)) if value else ""


def _iso(value):
    return value.isoformat() if value else ""


def _get(qs, what, text, hint="rename one of them first"):
    """The single match, or a clear error."""
    matches = list(qs[:2])
    if not matches:
        raise Missing(f"There's no {what} “{text}”.")
    if len(matches) > 1:
        raise RowError(f"There's more than one {what} “{text}”; {hint}.")
    return matches[0]


def location_named(text):
    return _get(Location.objects.filter(name__iexact=text.strip()), "location", text) if text else None


def device_named(text):
    return _get(Device.objects.filter(name__iexact=text.strip()), "device", text) if text else None


def port_of(device, text, column="Port"):
    if not text:
        return None
    try:
        return _get(device.ports.filter(name__iexact=text.strip()), f"port on {device.name}", text)
    except Missing:
        raise RowError(f"{column}: {device.name} has no port “{text}”. Add it (Ports sheet) first.") from None


def vlan_for(vid_text, location, column):
    """VLAN by ID: the one at `location` if there is one, otherwise the global one."""
    if not vid_text:
        return None
    if not vid_text.isdigit():
        raise RowError(f"{column}: give the VLAN ID as a number, not “{vid_text}”.")
    qs = VLAN.objects.filter(vid=int(vid_text))
    match = (qs.filter(location=location).first() if location else None) or qs.filter(location__isnull=True).first()
    if match is None:
        raise RowError(f"{column}: there's no VLAN {vid_text}{f' at {location} or' if location else ''} "
                       "used everywhere. Add it (VLANs sheet) first.")
    return match


def _pk(obj):
    return obj.pk if obj is not None else ""


class Resource:
    key = label = icon = ""
    model = None
    columns: list[Col] = []
    order = 0

    @property
    def keys(self):
        """Columns that identify a record; every file needs them (others only when creating)."""
        return [self.columns[0].header]

    @property
    def headers(self):
        return [c.header for c in self.columns]

    @property
    def import_headers(self):
        return [c.header for c in self.columns if not c.export_only]

    def find(self, v):
        """The existing record this row is about, or None."""
        raise NotImplementedError

    def values(self, obj):
        """{header: text} for one record (exports, and the starting point when a row updates it)."""
        raise NotImplementedError

    def form(self, v, obj):
        raise NotImplementedError

    def name(self, v):
        return v.get(self.columns[0].header, "")

    def queryset(self, **filters):
        return self.model.objects.all()

    def save(self, form):
        return form.save()

    def defaults(self):
        return {}


# --------------------------------------------------------------------------


class Locations(Resource):
    key, label, icon, model, order = "locations", "Locations", "map-pin", Location, 1
    columns = [Col("Name", "Unique name of the site / building / room", "HQ — Server Room", True),
               Col("Address", example="12 Marina Rd, Lagos"), Col("Notes")]

    def find(self, v):
        return Location.objects.filter(name__iexact=v["Name"]).first()

    def values(self, o):
        return {"Name": o.name, "Address": o.address, "Notes": o.notes}

    def form(self, v, obj):
        return LocationForm({"name": v["Name"], "address": v["Address"], "notes": v["Notes"]}, instance=obj)


class Racks(Resource):
    key, label, icon, model, order = "racks", "Racks", "layers", Rack, 2
    keys = ["Location", "Name"]
    columns = [Col("Location", "Must exist (Locations sheet)", "HQ — Server Room", True),
               Col("Name", "Unique within the location", "Rack 1", True),
               Col("Height (U)", "Default 42", "42"), Col("Notes")]

    def find(self, v):
        return Rack.objects.filter(location__name__iexact=v["Location"], name__iexact=v["Name"]).first()

    def name(self, v):
        return f"{v['Location']} · {v['Name']}"

    def defaults(self):
        return {"Height (U)": "42"}

    def values(self, o):
        return {"Location": o.location.name, "Name": o.name, "Height (U)": str(o.height_u), "Notes": o.notes}

    def queryset(self, **filters):
        return Rack.objects.select_related("location")

    def form(self, v, obj):
        return RackForm({"location": _pk(location_named(v["Location"])), "name": v["Name"],
                         "height_u": v["Height (U)"], "notes": v["Notes"]}, instance=obj)


class VLANs(Resource):
    key, label, icon, model, order = "vlans", "VLANs", "hash", VLAN, 3
    keys = ["VLAN ID"]
    columns = [Col("VLAN ID", "1–4094", "10", True), Col("Name", example="Management", required=True),
               Col("Location", "Empty = used everywhere"), Col("Description")]

    def find(self, v):
        qs = VLAN.objects.filter(vid=v["VLAN ID"]) if v["VLAN ID"].isdigit() else VLAN.objects.none()
        loc = v["Location"]
        return (qs.filter(location__name__iexact=loc) if loc else qs.filter(location__isnull=True)).first()

    def name(self, v):
        return f"{v['VLAN ID']} · {v['Name']}"

    def values(self, o):
        return {"VLAN ID": str(o.vid), "Name": o.name, "Location": o.location.name if o.location else "",
                "Description": o.description}

    def queryset(self, **filters):
        return VLAN.objects.select_related("location")

    def form(self, v, obj):
        return VLANForm({"vid": v["VLAN ID"], "name": v["Name"], "location": _pk(location_named(v["Location"])),
                         "description": v["Description"]}, instance=obj)


class Devices(Resource):
    key, label, icon, model, order = "devices", "Devices", "server", Device, 4
    columns = [
        Col("Name", "Unique device name", "SW-CORE-01", True),
        Col("Purpose", "What it does", "Core switching; carries the internal VLANs"),
        Col("Type", "Switch, Router, Firewall, Server, Access point, Camera, NVR, Patch panel, UPS, …", "Switch"),
        Col("Status", "Active, Spare, Faulty or Retired (default Active)", "Active"),
        Col("Manufacturer", example="Cisco", required=True), Col("Model", example="C9300-48P", required=True),
        Col("Location", "Not needed when a rack is given", "HQ — Server Room"),
        Col("Rack", "Rack name in that location; empty = not racked", "Rack 1"),
        Col("Position (U)", "Lowest U the device occupies", "40"), Col("Height (U)", "Default 1", "1"),
        Col("Face", "Front or Rear (default Front)", "Front"),
        Col("Management IP", example="10.10.0.2"), Col("Serial"), Col("Asset tag"), Col("Firmware"), Col("MAC"),
        Col("Parent", "Upstream device it depends on (for unreachable alerts)", "FW-EDGE-01"),
        Col("Purchase date", "2026-11-30 or 30/11/2026"), Col("Warranty until"), Col("Owner"), Col("Notes"),
    ]

    def find(self, v):
        return Device.objects.filter(name__iexact=v["Name"]).first()

    def defaults(self):
        return {"Type": "Other", "Status": "Active", "Height (U)": "1", "Face": "Front"}

    def queryset(self, rack=None, **filters):
        qs = Device.objects.select_related("device_model__manufacturer", "location", "rack", "parent")
        return qs.filter(rack_id=rack) if rack else qs

    def values(self, d):
        return {
            "Name": d.name, "Purpose": d.purpose, "Type": d.get_device_type_display(),
            "Status": d.get_status_display(),
            "Manufacturer": d.device_model.manufacturer.name, "Model": d.device_model.name,
            "Location": d.location.name, "Rack": d.rack.name if d.rack else "",
            "Position (U)": str(d.position_u or ""), "Height (U)": str(d.height_u), "Face": d.get_face_display(),
            "Management IP": d.mgmt_ip or "", "Serial": d.serial, "Asset tag": d.asset_tag or "",
            "Firmware": d.firmware, "MAC": d.mac, "Parent": d.parent.name if d.parent else "",
            "Purchase date": _iso(d.purchase_date), "Warranty until": _iso(d.warranty_expiry), "Owner": d.owner,
            "Notes": d.notes,
        }

    def form(self, v, obj):
        location = location_named(v["Location"]) if v["Location"] else None
        rack = None
        if v["Rack"]:
            racks = Rack.objects.filter(name__iexact=v["Rack"])
            if location:
                racks = racks.filter(location=location)
            rack = _get(racks, f"rack in {location}" if location else "rack", v["Rack"],
                        hint="rename one of them first" if location else "fill in the Location column too")
            location = rack.location
        if location is None:
            raise RowError("Location: give a location (or a rack).")
        parent = device_named(v["Parent"])
        return DeviceForm({
            "name": v["Name"], "purpose": v["Purpose"],
            "device_type": choice(Device, "device_type", v["Type"], "Type") or "other",
            "status": choice(Device, "status", v["Status"], "Status") or "active",
            "manufacturer_name": v["Manufacturer"], "model_name": v["Model"],
            "location": location.pk, "rack": _pk(rack), "position_u": v["Position (U)"],
            "height_u": v["Height (U)"] or "1", "face": choice(Device, "face", v["Face"], "Face") or "front",
            "mgmt_ip": v["Management IP"], "serial": v["Serial"], "asset_tag": v["Asset tag"],
            "firmware": v["Firmware"], "mac": v["MAC"], "parent": _pk(parent),
            "purchase_date": date(v["Purchase date"], "Purchase date"),
            "warranty_expiry": date(v["Warranty until"], "Warranty until"), "owner": v["Owner"], "notes": v["Notes"],
        }, instance=obj)


class Ports(Resource):
    key, label, icon, model, order = "ports", "Ports", "ethernet-port", Port, 5
    keys = ["Device", "Port"]
    columns = [
        Col("Device", "Must exist (Devices sheet)", "SW-CORE-01", True),
        Col("Port", "Port name as on the device", "Gi1/0/1", True), Col("Label", "Printed label, if different"),
        Col("Type", "Copper (RJ45), SFP / SFP+, Fibre, Console, Patch panel — front / rear, Other", "Copper (RJ45)"),
        Col("Speed", "100 Mb, 1 Gb, 2.5 Gb, 10 Gb, 25 Gb, 40 Gb, 100 Gb", "1 Gb"),
        Col("Mode", "Access, Trunk or Routed", "Access"),
        Col("Untagged VLAN", "VLAN ID", "10"), Col("Tagged VLANs", "VLAN IDs, comma-separated", "20, 30"),
        Col("Remark", "What it's for", "Uplink to FW"), Col("Enabled", "yes / no (default yes)", "yes"),
        Col("Connected to", export_only=True), Col("Connection function", export_only=True),
    ]

    def find(self, v):
        return Port.objects.filter(device__name__iexact=v["Device"], name__iexact=v["Port"]).first()

    def name(self, v):
        return f"{v['Device']} · {v['Port']}"

    def defaults(self):
        return {"Type": "Copper (RJ45)", "Enabled": "yes"}

    def queryset(self, device=None, **filters):
        qs = (Port.objects.select_related("device", "untagged_vlan", "connection_a__b_port__device",
                                          "connection_a__b_external", "connection_b__a_port__device")
              .prefetch_related("tagged_vlans").order_by("device__name", "sort_key"))
        return qs.filter(device_id=device) if device else qs

    def values(self, p):
        connected, function = "", ""
        conn = getattr(p, "connection_a", None) or getattr(p, "connection_b", None)
        if conn is not None:
            other = conn.b_port if conn.a_port_id == p.pk else conn.a_port
            connected = f"{other.device.name} · {other.name}" if other else str(conn.b_external or "")
            function = conn.function
        return {
            "Device": p.device.name, "Port": p.name, "Label": p.label, "Type": p.get_type_display(),
            "Speed": p.get_speed_display() if p.speed else "", "Mode": p.get_mode_display() if p.mode else "",
            "Untagged VLAN": str(p.untagged_vlan.vid) if p.untagged_vlan else "",
            "Tagged VLANs": ", ".join(str(t.vid) for t in sorted(p.tagged_vlans.all(), key=lambda t: t.vid)),
            "Remark": p.remark, "Enabled": "yes" if p.enabled else "no",
            "Connected to": connected, "Connection function": function,
        }

    def form(self, v, obj):
        device = device_named(v["Device"])
        location = device.location
        tagged = [t.strip() for t in re.split(r"[,;\s]+", v["Tagged VLANs"]) if t.strip()]
        return PortForm({
            "name": v["Port"], "label": v["Label"], "type": choice(Port, "type", v["Type"], "Type") or "copper",
            "speed": choice(Port, "speed", v["Speed"], "Speed"), "mode": choice(Port, "mode", v["Mode"], "Mode"),
            "untagged_vlan": _pk(vlan_for(v["Untagged VLAN"], location, "Untagged VLAN")),
            "tagged_vlans": [vlan_for(t, location, "Tagged VLANs").pk for t in tagged],
            "remark": v["Remark"], "enabled": boolean(v["Enabled"] or "yes", "Enabled"),
        }, instance=obj or Port(device=device))


class Connections(Resource):
    key, label, icon, model, order = "connections", "Connections", "cable", Connection, 6
    keys = ["Device A", "Port A"]
    columns = [
        Col("Device A", "Must exist", "SW-CORE-01", True), Col("Port A", "Must exist on device A", "Te1/1/1", True),
        Col("Device B", "Empty when the other end is external", "FW-EDGE-01"), Col("Port B", example="port3"),
        Col("External B", "Other end outside the inventory, e.g. “ISP ONT — circuit FL-22817”"),
        Col("Function", "What the link is for", "Uplink to firewall", True),
        Col("Cable label", example="C-0012"),
        Col("Cable type", "Cat5e, Cat6, Cat6a, Single-mode fibre, Multi-mode fibre, DAC, Other", "Cat6"),
        Col("Colour", example="Blue"), Col("Length (m)", example="2.5"), Col("Remark"),
    ]

    def _a_port(self, v):
        return port_of(device_named(v["Device A"]), v["Port A"], "Port A")

    def find(self, v):
        try:
            port = self._a_port(v)
        except RowError:
            return None
        return Connection.objects.filter(Q(a_port=port) | Q(b_port=port)).first() if port else None

    def name(self, v):
        b = f"{v['Device B']} · {v['Port B']}" if v["Device B"] else v["External B"]
        return f"{v['Device A']} · {v['Port A']} ↔ {b}"

    def queryset(self, **filters):
        return Connection.objects.select_related("a_port__device", "b_port__device", "b_external")

    def values(self, c):
        return {
            "Device A": c.a_port.device.name, "Port A": c.a_port.name,
            "Device B": c.b_port.device.name if c.b_port else "", "Port B": c.b_port.name if c.b_port else "",
            "External B": c.b_external.name if c.b_external else "", "Function": c.function,
            "Cable label": c.cable_label, "Cable type": _label(Connection, "cable_type", c.cable_type),
            "Colour": c.colour, "Length (m)": str(c.length_m or ""), "Remark": c.remark,
        }

    def form(self, v, obj):
        a_device = device_named(v["Device A"])
        a_port = port_of(a_device, v["Port A"], "Port A")
        data = {"a_device": a_device.pk, "a_port": a_port.pk, "function": v["Function"],
                "cable_label": v["Cable label"], "cable_type": choice(Connection, "cable_type", v["Cable type"],
                                                                      "Cable type"),
                "colour": v["Colour"], "length_m": v["Length (m)"], "remark": v["Remark"],
                # The form can also set port VLANs; pass the current ones so nothing looks changed.
                "untagged_vlan": a_port.untagged_vlan_id or "",
                "tagged_vlans": list(a_port.tagged_vlans.values_list("pk", flat=True))}
        if v["Device B"]:
            b_device = device_named(v["Device B"])
            data.update(b_kind="device", b_device=b_device.pk, b_port=_pk(port_of(b_device, v["Port B"], "Port B")))
        elif v["External B"]:
            data.update(b_kind="external", b_external_name=v["External B"])
        else:
            raise RowError("Give Device B and Port B, or External B.")
        if obj is not None and obj.b_port_id == a_port.pk:
            obj.a_port, obj.b_port = obj.b_port, obj.a_port  # same cable, listed from the other end
        return ConnectionForm(data, instance=obj)


class Prefixes(Resource):
    key, label, icon, model, order = "prefixes", "Prefixes", "globe", Prefix, 7
    columns = [
        Col("Prefix", "Network with length", "129.205.100.128/28", True),
        Col("Kind", "Public or Private (empty = detected)"), Col("Status", "Active, Reserved or Deprecated"),
        Col("Gateway", example="129.205.100.129"), Col("ISP", "Must exist (ISPs page)", "FibreLink"),
        Col("Circuit", "Circuit ID", "FL-22817"), Col("VLAN", "VLAN ID", "10"), Col("Location"),
        Col("Description", example="Public block from FibreLink"),
    ]

    def find(self, v):
        try:
            net, _ = parse_network(v["Prefix"])
        except Exception:  # noqa: BLE001 — the form reports the bad prefix
            return None
        return Prefix.objects.filter(cidr=str(net)).first()

    def defaults(self):
        return {"Status": "Active"}

    def queryset(self, **filters):
        return Prefix.objects.select_related("isp", "circuit", "vlan", "location").order_by("network_int")

    def values(self, p):
        return {"Prefix": p.cidr, "Kind": p.get_kind_display(), "Status": p.get_status_display(),
                "Gateway": p.gateway or "", "ISP": p.isp.name if p.isp else "",
                "Circuit": p.circuit.circuit_id if p.circuit else "", "VLAN": str(p.vlan.vid) if p.vlan else "",
                "Location": p.location.name if p.location else "", "Description": p.description}

    def form(self, v, obj):
        location = location_named(v["Location"])
        isp = _get(ISP.objects.filter(name__iexact=v["ISP"]), "ISP", v["ISP"]) if v["ISP"] else None
        circuit = (_get(Circuit.objects.filter(circuit_id__iexact=v["Circuit"]), "circuit", v["Circuit"])
                   if v["Circuit"] else None)
        return PrefixForm({
            "cidr": v["Prefix"], "kind": choice(Prefix, "kind", v["Kind"], "Kind"),
            "status": choice(Prefix, "status", v["Status"], "Status") or "active", "gateway": v["Gateway"],
            "isp": _pk(isp), "circuit": _pk(circuit), "vlan": _pk(vlan_for(v["VLAN"], location, "VLAN")),
            "location": _pk(location), "description": v["Description"],
        }, instance=obj)


class IPAddresses(Resource):
    key, label, icon, model, order = "ips", "IP addresses", "map-pin", IPAddress, 8
    columns = [
        Col("Address", example="129.205.100.131", required=True), Col("Status", "Used, Reserved or DHCP pool"),
        Col("Device", example="FW-EDGE-01"), Col("Port", "Port on that device", "port1"),
        Col("Service", "What uses it", "Customer portal"), Col("DNS name", example="portal.example.com"),
        Col("NAT to", "Inside address", "10.10.10.11"), Col("NAT ports", example="443, 80"), Col("Description"),
    ]

    def find(self, v):
        return IPAddress.objects.filter(address=v["Address"].strip()).first()

    def defaults(self):
        return {"Status": "Used"}

    def queryset(self, **filters):
        return IPAddress.objects.select_related("device", "port").order_by("address_int")

    def values(self, ip):
        return {"Address": ip.address, "Status": ip.get_status_display(), "Device": ip.device.name if ip.device else "",
                "Port": ip.port.name if ip.port else "", "Service": ip.service, "DNS name": ip.dns_name,
                "NAT to": ip.nat_to or "", "NAT ports": ip.nat_ports, "Description": ip.description}

    def form(self, v, obj):
        device = device_named(v["Device"])
        port = port_of(device, v["Port"]) if device and v["Port"] else None
        if v["Port"] and not device:
            raise RowError("Port: give the device the port is on.")
        return IPAddressForm({
            "address": v["Address"], "status": choice(IPAddress, "status", v["Status"], "Status") or "used",
            "device": _pk(device), "port": _pk(port), "service": v["Service"], "dns_name": v["DNS name"],
            "nat_to": v["NAT to"], "nat_ports": v["NAT ports"], "description": v["Description"],
        }, instance=obj)


class Monitors(Resource):
    key, label, icon, model, order = "monitors", "Monitors", "activity", Monitor, 9
    columns = [
        Col("Name", example="Core switch", required=True),
        Col("Type", "Ping, TCP port, HTTP, HTTP keyword, DNS record or SSL certificate", "Ping", True),
        Col("Target", "Host / IP, or the URL for HTTP", "10.10.0.2", True), Col("Port", "For TCP and SSL"),
        Col("Interval (s)", "Default 60, minimum 20", "60"), Col("Timeout (s)", "Default 10", "10"),
        Col("Down after", "Failed checks in a row (default 3)", "3"), Col("Device", example="SW-CORE-01"),
        Col("Depends on", "Name of the parent monitor"), Col("Accepted status codes", "HTTP, default 200-299"),
        Col("Keyword", "HTTP keyword checks"), Col("Follow redirects", "yes / no"),
        Col("Ignore certificate errors", "yes / no"), Col("DNS record type", "A, AAAA, CNAME, MX, TXT, NS"),
        Col("DNS expected"), Col("SSL warn days", "Default 14"), Col("Enabled", "yes / no (default yes)"),
    ]

    def find(self, v):
        found = list(Monitor.objects.filter(name__iexact=v["Name"])[:2])
        if len(found) > 1:
            raise RowError(f"More than one monitor is called “{v['Name']}”; rename one of them first.")
        return found[0] if found else None

    def defaults(self):
        return {"Interval (s)": "60", "Timeout (s)": "10", "Down after": "3", "Accepted status codes": "200-299",
                "Follow redirects": "yes", "Ignore certificate errors": "no", "DNS record type": "A",
                "SSL warn days": "14", "Enabled": "yes"}

    def queryset(self, **filters):
        return Monitor.objects.select_related("device", "parent")

    def values(self, m):
        return {
            "Name": m.name, "Type": m.get_type_display(), "Target": m.target, "Port": str(m.port or ""),
            "Interval (s)": str(m.interval_s), "Timeout (s)": str(m.timeout_s), "Down after": str(m.retries),
            "Device": m.device.name if m.device else "", "Depends on": m.parent.name if m.parent else "",
            "Accepted status codes": m.expected_status, "Keyword": m.keyword,
            "Follow redirects": "yes" if m.follow_redirects else "no",
            "Ignore certificate errors": "yes" if m.ignore_tls else "no", "DNS record type": m.dns_record_type,
            "DNS expected": m.dns_expected, "SSL warn days": str(m.ssl_warn_days),
            "Enabled": "yes" if m.enabled else "no",
        }

    def form(self, v, obj):
        parent = (_get(Monitor.objects.filter(name__iexact=v["Depends on"]), "monitor", v["Depends on"])
                  if v["Depends on"] else None)
        return MonitorForm({
            "name": v["Name"], "type": choice(Monitor, "type", v["Type"], "Type"), "target": v["Target"],
            "port": v["Port"], "interval_s": v["Interval (s)"], "timeout_s": v["Timeout (s)"],
            "retries": v["Down after"], "http_method": obj.http_method if obj else "GET",
            "expected_status": v["Accepted status codes"],
            "keyword": v["Keyword"], "follow_redirects": boolean(v["Follow redirects"], "Follow redirects"),
            "ignore_tls": boolean(v["Ignore certificate errors"], "Ignore certificate errors"),
            "dns_record_type": choice(Monitor, "dns_record_type", v["DNS record type"], "DNS record type"),
            "dns_expected": v["DNS expected"], "ssl_warn_days": v["SSL warn days"],
            "device": _pk(device_named(v["Device"])), "parent": _pk(parent),
            "enabled": boolean(v["Enabled"] or "yes", "Enabled"),
        }, instance=obj)


RESOURCES = {r.key: r for r in sorted((cls() for cls in Resource.__subclasses__()), key=lambda r: r.order)}


def for_sheet(title):
    """The resource a sheet / file name refers to ("Devices", "devices.csv", "IP addresses"…), or None."""
    t = re.sub(r"[^a-z]", "", title.lower())
    for r in RESOURCES.values():
        if t in {r.key, re.sub(r"[^a-z]", "", r.label.lower()), r.key.rstrip("s")}:
            return r
    return None
