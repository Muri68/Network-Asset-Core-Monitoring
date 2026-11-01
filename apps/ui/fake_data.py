"""
Fake, internally consistent data for the UI prototype.

Every page reads from here, so the same firewall shows up on the rack,
in the port map, in monitoring and in the vault. Replaced by real models
from Phase 1 onwards (see PLAN.md).
"""

import ipaddress
import random
from datetime import timedelta

from django.utils import timezone

NOW = timezone.now()


def ago(**kw):
    return NOW - timedelta(**kw)


def ahead(**kw):
    return NOW + timedelta(**kw)


ORG_NAME = "Acme Networks"

# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------

USERS = [
    {"id": 1, "name": "Musa Ibrahim", "username": "musa", "email": "musa@acme.example", "role": "Admin",
     "active": True, "twofa": True, "telegram": True, "reveal_code": True, "last_login": ago(minutes=4),
     "vault_locked": False},
    {"id": 2, "name": "Aisha Bello", "username": "aisha", "email": "aisha@acme.example", "role": "Engineer",
     "active": True, "twofa": True, "telegram": True, "reveal_code": True, "last_login": ago(hours=2),
     "vault_locked": False},
    {"id": 3, "name": "Chinedu Okafor", "username": "chinedu", "email": "chinedu@acme.example", "role": "Engineer",
     "active": True, "twofa": True, "telegram": False, "reveal_code": True, "last_login": ago(days=1, hours=3),
     "vault_locked": True},
    {"id": 4, "name": "Grace Adeyemi", "username": "grace", "email": "grace@acme.example", "role": "Viewer",
     "active": True, "twofa": True, "telegram": True, "reveal_code": False, "last_login": ago(days=3),
     "vault_locked": False},
    {"id": 5, "name": "John Danjuma", "username": "john", "email": "john@acme.example", "role": "Viewer",
     "active": False, "twofa": False, "telegram": False, "reveal_code": False, "last_login": ago(days=64),
     "vault_locked": False},
]
CURRENT_USER = USERS[0]

ROLE_MATRIX = [
    ("View inventory, racks, ports, IPAM, monitors", True, True, True),
    ("Create / edit inventory, ports, IPAM, monitors", True, True, False),
    ("See vault entries", True, True, False),
    ("Reveal / copy passwords (with reveal code)", True, True, False),
    ("View reveal logs of all users", True, False, False),
    ("Import data", True, False, False),
    ("Export data", True, True, False),
    ("Manage users, roles, unlock vault", True, False, False),
    ("Audit log & system settings", True, False, False),
]

# --------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------

DEVICE_TYPES = {
    "firewall": {"label": "Firewall", "icon": "flame"},
    "router": {"label": "Router", "icon": "router"},
    "switch": {"label": "Switch", "icon": "network"},
    "server": {"label": "Server", "icon": "server"},
    "storage": {"label": "Storage", "icon": "database"},
    "nvr": {"label": "NVR", "icon": "hard-drive"},
    "camera": {"label": "Camera", "icon": "cctv"},
    "access_control": {"label": "Access control", "icon": "fingerprint"},
    "access_point": {"label": "Access point", "icon": "wifi"},
    "patch_panel": {"label": "Patch panel", "icon": "ethernet-port"},
    "ups": {"label": "UPS", "icon": "battery-charging"},
}

LOCATIONS = [
    {"id": 1, "name": "HQ — Server Room", "address": "Ground floor, Block A"},
    {"id": 2, "name": "HQ — Campus", "address": "Gates, corridors, car park"},
]

RACKS = [
    {"id": 1, "name": "Rack 1 — Core", "location": "HQ — Server Room", "height": 42},
    {"id": 2, "name": "Rack 2 — Servers", "location": "HQ — Server Room", "height": 42},
    {"id": 3, "name": "Rack 3 — Security", "location": "HQ — Server Room", "height": 42},
]

_D = [
    # id, name, type, manufacturer, model, mgmt_ip, rack, U, height, parent, status
    (1, "RTR-EDGE-01", "router", "MikroTik", "CCR2004-16G-2S+", "10.10.0.254", 1, 42, 1, None, "active"),
    (2, "FW-CORE-01", "firewall", "Fortinet", "FortiGate 200F", "10.10.0.1", 1, 41, 1, 1, "active"),
    (3, "PP-R1-01", "patch_panel", "Panduit", "24-port Cat6A", None, 1, 40, 1, None, "active"),
    (4, "SW-CORE-01", "switch", "Cisco", "Catalyst 9300-48P", "10.10.0.2", 1, 38, 1, 2, "active"),
    (5, "SW-CORE-02", "switch", "Cisco", "Catalyst 9300-48P", "10.10.0.3", 1, 37, 1, 2, "active"),
    (6, "PP-R1-02", "patch_panel", "Panduit", "24-port Cat6A", None, 1, 36, 1, None, "active"),
    (7, "UPS-R1", "ups", "APC", "Smart-UPS SRT 3000", "10.10.0.30", 1, 1, 2, None, "active"),
    (8, "SW-SRV-01", "switch", "Cisco", "Catalyst 9200-24T", "10.10.0.4", 2, 40, 1, 4, "active"),
    (9, "SRV-HV-01", "server", "HPE", "ProLiant DL380 Gen11", "10.10.10.5", 2, 36, 2, 8, "active"),
    (10, "SRV-HV-02", "server", "HPE", "ProLiant DL380 Gen11", "10.10.10.6", 2, 33, 2, 8, "active"),
    (11, "SRV-WEB-01", "server", "Dell", "PowerEdge R650", "10.10.10.11", 2, 30, 1, 8, "active"),
    (12, "SRV-WEB-02", "server", "Dell", "PowerEdge R650", "10.10.10.12", 2, 29, 1, 8, "active"),
    (13, "SRV-DB-01", "server", "Dell", "PowerEdge R750", "10.10.10.21", 2, 26, 2, 8, "active"),
    (14, "STOR-01", "storage", "Dell", "PowerVault ME5012", "10.10.10.40", 2, 22, 2, 8, "active"),
    (15, "UPS-R2", "ups", "APC", "Smart-UPS SRT 3000", "10.10.0.31", 2, 1, 2, None, "active"),
    (16, "SW-CCTV-01", "switch", "Cisco", "CBS350-24FP", "10.20.0.2", 3, 40, 1, 5, "active"),
    (17, "PP-R3-01", "patch_panel", "Panduit", "24-port Cat6", None, 3, 39, 1, None, "active"),
    (18, "NVR-01", "nvr", "Hikvision", "DS-9664NI-M8", "10.20.0.10", 3, 34, 2, 16, "active"),
    (19, "NVR-02", "nvr", "Hikvision", "DS-9632NI-M8", "10.20.0.11", 3, 31, 2, 16, "active"),
    (20, "ACS-01", "access_control", "ZKTeco", "inBio460 Pro", "10.30.0.10", 3, 28, 1, 5, "active"),
    (21, "SW-SPARE-01", "switch", "Cisco", "Catalyst 9200-24T", None, None, None, 1, None, "spare"),
    (22, "CAM-LOBBY-01", "camera", "Hikvision", "DS-2CD2347G2-LU", "10.20.1.21", None, None, 0, 16, "active"),
    (23, "CAM-GATE-01", "camera", "Hikvision", "DS-2CD2T87G2-L", "10.20.1.22", None, None, 0, 16, "active"),
    (24, "CAM-PARK-01", "camera", "Hikvision", "DS-2CD2T87G2-L", "10.20.1.23", None, None, 0, 16, "faulty"),
    (25, "CAM-SRVRM-01", "camera", "Hikvision", "DS-2CD2147G2-SU", "10.20.1.24", None, None, 0, 16, "active"),
    (26, "DOOR-SRVRM-01", "access_control", "ZKTeco", "SpeedFace-V5L", "10.30.0.21", None, None, 0, 20, "active"),
    (27, "AP-FL1-01", "access_point", "Ubiquiti", "U6 Pro", "10.40.0.21", None, None, 0, 4, "active"),
    (28, "AP-FL2-01", "access_point", "Ubiquiti", "U6 Pro", "10.40.0.22", None, None, 0, 4, "active"),
    (29, "AP-RECEP-01", "access_point", "Ubiquiti", "U6 Lite", "10.40.0.23", None, None, 0, 4, "active"),
    (30, "SRV-OLD-WEB", "server", "Dell", "PowerEdge R630", None, None, None, 1, None, "retired"),
]

_rng = random.Random(42)
DEVICES = []
for (did, name, dtype, mfr, model, ip, rack, u, h, parent, status) in _D:
    DEVICES.append({
        "id": did, "name": name, "type": dtype, "type_label": DEVICE_TYPES[dtype]["label"],
        "icon": DEVICE_TYPES[dtype]["icon"], "manufacturer": mfr, "model": model, "mgmt_ip": ip,
        "rack_id": rack, "rack": next((r["name"] for r in RACKS if r["id"] == rack), None),
        "position_u": u, "height_u": h, "face": "Front", "parent_id": parent, "status": status,
        "location": "HQ — Server Room" if rack else "HQ — Campus",
        "serial": f"{mfr[:3].upper()}{_rng.randint(10**7, 10**8 - 1)}",
        "asset_tag": f"ACME-{did:04d}",
        "firmware": {"firewall": "FortiOS 7.4.5", "switch": "IOS-XE 17.12.4", "router": "RouterOS 7.16",
                     "nvr": "V4.74.105", "camera": "V5.8.10"}.get(dtype, "—"),
        "mac": ":".join(f"{_rng.randint(0, 255):02x}" for _ in range(6)) if ip else "—",
        "purchase_date": (NOW - timedelta(days=_rng.randint(200, 1400))).date(),
        "warranty_expiry": (NOW + timedelta(days=_rng.randint(-60, 900))).date(),
        "owner": _rng.choice(["Network team", "Infrastructure", "Security team"]),
        "notes": "",
        "attributes": {},
    })
DEVICES_BY_ID = {d["id"]: d for d in DEVICES}
DEVICES_BY_NAME = {d["name"]: d for d in DEVICES}

DEVICES_BY_ID[2]["notes"] = "Primary edge firewall. HA pair planned for Q1."
DEVICES_BY_ID[2]["warranty_expiry"] = (NOW + timedelta(days=41)).date()
DEVICES_BY_ID[19]["warranty_expiry"] = (NOW + timedelta(days=17)).date()
DEVICES_BY_ID[13]["warranty_expiry"] = (NOW + timedelta(days=72)).date()
DEVICES_BY_ID[2]["attributes"] = {"HA role": "Standalone", "License": "UTP bundle — expires 2027-03-14",
                                  "Console": "RJ45 (9600 8N1)"}
DEVICES_BY_ID[18]["attributes"] = {"Channels": "64", "HDD": "8 × 8 TB (RAID 5)", "Retention": "30 days"}
DEVICES_BY_ID[22]["attributes"] = {"NVR channel": "NVR-01 / CH 7", "Resolution": "4 MP", "PoE": "SW-CCTV-01 Gi5"}
DEVICES_BY_ID[23]["attributes"] = {"NVR channel": "NVR-01 / CH 1", "Resolution": "8 MP", "PoE": "SW-CCTV-01 Gi6"}

# --------------------------------------------------------------------------
# VLANs
# --------------------------------------------------------------------------

VLANS = [
    {"vid": 1, "name": "Native / unused", "subnet": "—", "devices": 0, "desc": "Not used — native VLAN parked"},
    {"vid": 10, "name": "Management", "subnet": "10.10.0.0/24", "devices": 12, "desc": "Network device management"},
    {"vid": 20, "name": "Servers", "subnet": "10.10.10.0/24", "devices": 8, "desc": "Hypervisors, web, DB, storage"},
    {"vid": 30, "name": "CCTV", "subnet": "10.20.0.0/22", "devices": 6, "desc": "NVRs and IP cameras"},
    {"vid": 40, "name": "Access control", "subnet": "10.30.0.0/24", "devices": 2, "desc": "Door controllers & readers"},
    {"vid": 50, "name": "Staff Wi-Fi", "subnet": "10.40.0.0/23", "devices": 3, "desc": "Corporate SSID"},
    {"vid": 60, "name": "Guest Wi-Fi", "subnet": "192.168.100.0/24", "devices": 3, "desc": "Isolated guest SSID"},
    {"vid": 99, "name": "WAN transit", "subnet": "129.205.100.128/28", "devices": 2, "desc": "Public block transit"},
]

# --------------------------------------------------------------------------
# Ports & connections
# --------------------------------------------------------------------------

EXTERNAL = {
    "ext-fibre": "FibreLink ONT — circuit FL-ABJ-22817",
    "ext-radio": "SkyWave radio — circuit SW-00931",
    "ext-jack-b12": "Wall jack B-12 (Reception)",
}

# a_dev, a_port, b_dev, b_port (or ext key), function, mode, vlans, cable, remark
_C = [
    ("RTR-EDGE-01", "sfp-sfpplus1", None, "ext-fibre", "Primary internet (FibreLink)", "routed", "—", "FO-001", "SM fibre, LC/UPC"),
    ("RTR-EDGE-01", "ether2", None, "ext-radio", "Backup internet (SkyWave)", "routed", "—", "C-002", ""),
    ("RTR-EDGE-01", "ether1", "FW-CORE-01", "wan1", "WAN uplink — public block", "routed", "99", "C-003", ""),
    ("FW-CORE-01", "port1", "SW-CORE-01", "Gi1/0/10", "Internet + VLAN trunk", "trunk", "10,20,30,40,50,60", "C-010", "LACP member 1"),
    ("FW-CORE-01", "port2", "PP-R1-01", "2", "Internet + VLAN trunk", "trunk", "10,20,30,40,50,60", "C-011", "LACP member 2 via patch"),
    ("PP-R1-01", "2 (rear)", "SW-CORE-02", "Gi1/0/10", "Internet + VLAN trunk", "trunk", "10,20,30,40,50,60", "PC-011", ""),
    ("FW-CORE-01", "mgmt", "SW-CORE-01", "Gi1/0/1", "Firewall OOB management", "access", "10", "C-012", ""),
    ("SW-CORE-01", "Te1/1/1", "SW-SRV-01", "Te1/1/1", "Server switch uplink", "trunk", "10,20", "FO-020", "10G SFP+ DAC"),
    ("SW-CORE-02", "Te1/1/1", "SW-CCTV-01", "Gi25", "CCTV switch uplink", "trunk", "10,30,40", "FO-021", ""),
    ("SW-CORE-01", "Te1/1/4", "SW-CORE-02", "Te1/1/4", "Stack / inter-switch link", "trunk", "all", "DAC-01", ""),
    ("SW-SRV-01", "Gi1/0/1", "SRV-HV-01", "eno1", "Hypervisor uplink", "trunk", "10,20", "C-030", ""),
    ("SW-SRV-01", "Gi1/0/2", "SRV-HV-02", "eno1", "Hypervisor uplink", "trunk", "10,20", "C-031", ""),
    ("SW-SRV-01", "Gi1/0/3", "SRV-WEB-01", "eno1", "Web server", "access", "20", "C-032", ""),
    ("SW-SRV-01", "Gi1/0/4", "SRV-WEB-02", "eno1", "Web server", "access", "20", "C-033", ""),
    ("SW-SRV-01", "Gi1/0/5", "SRV-DB-01", "eno1", "Database server", "access", "20", "C-034", ""),
    ("SW-SRV-01", "Gi1/0/6", "STOR-01", "mgmt", "Storage management", "access", "20", "C-035", ""),
    ("SW-CCTV-01", "Gi1", "NVR-01", "LAN1", "NVR uplink", "access", "30", "C-040", ""),
    ("SW-CCTV-01", "Gi2", "NVR-02", "LAN1", "NVR uplink", "access", "30", "C-041", ""),
    ("SW-CCTV-01", "Gi5", "CAM-LOBBY-01", "eth0", "PoE camera — lobby", "access", "30", "C-042", ""),
    ("SW-CCTV-01", "Gi6", "CAM-GATE-01", "eth0", "PoE camera — main gate", "access", "30", "C-043", "Outdoor run, 74 m"),
    ("SW-CCTV-01", "Gi7", "CAM-PARK-01", "eth0", "PoE camera — car park", "access", "30", "C-044", "Camera faulty — RMA open"),
    ("SW-CCTV-01", "Gi8", "CAM-SRVRM-01", "eth0", "PoE camera — server room", "access", "30", "C-045", ""),
    ("SW-CORE-02", "Gi1/0/20", "ACS-01", "LAN", "Access control panel", "access", "40", "C-050", ""),
    ("SW-CORE-01", "Gi1/0/30", "AP-FL1-01", "eth0", "Wi-Fi AP floor 1", "trunk", "50,60", "C-060", ""),
    ("SW-CORE-01", "Gi1/0/31", "AP-FL2-01", "eth0", "Wi-Fi AP floor 2", "trunk", "50,60", "C-061", ""),
    ("SW-CORE-01", "Gi1/0/32", "PP-R1-02", "5", "Reception AP via patch", "trunk", "50,60", "C-062", ""),
    ("PP-R1-02", "5 (rear)", None, "ext-jack-b12", "Reception AP via patch", "trunk", "50,60", "H-B12", "Horizontal run to B-12"),
    ("SW-CORE-01", "Gi1/0/2", "UPS-R1", "NMC", "UPS management", "access", "10", "C-070", ""),
]

CONNECTIONS = []
for i, (a, ap, b, bp, fn, mode, vlans, cable, remark) in enumerate(_C, start=1):
    CONNECTIONS.append({
        "id": i,
        "a_device": DEVICES_BY_NAME[a], "a_port": ap,
        "b_device": DEVICES_BY_NAME[b] if b else None, "b_port": bp if b else None,
        "b_external": EXTERNAL[bp] if not b else None,
        "function": fn, "mode": mode, "vlans": vlans, "cable": cable, "remark": remark,
    })


def _port_names(dev):
    t = dev["type"]
    if dev["name"].startswith("SW-CORE"):
        return [f"Gi1/0/{i}" for i in range(1, 49)] + [f"Te1/1/{i}" for i in range(1, 5)]
    if t == "switch" and "CBS" in dev["model"]:
        return [f"Gi{i}" for i in range(1, 25)] + ["Gi25", "Gi26"]
    if t == "switch":
        return [f"Gi1/0/{i}" for i in range(1, 25)] + [f"Te1/1/{i}" for i in range(1, 5)]
    if t == "firewall":
        return ["wan1", "wan2", "mgmt", "ha"] + [f"port{i}" for i in range(1, 17)]
    if t == "router":
        return [f"ether{i}" for i in range(1, 13)] + ["sfp-sfpplus1", "sfp-sfpplus2"]
    if t == "patch_panel":
        return [str(i) for i in range(1, 25)]
    if t == "server":
        return ["eno1", "eno2", "eno3", "eno4", "iLO" if dev["manufacturer"] == "HPE" else "iDRAC"]
    if t == "storage":
        return ["mgmt", "A0", "A1", "B0", "B1"]
    if t == "nvr":
        return ["LAN1", "LAN2"]
    if t == "ups":
        return ["NMC"]
    if t == "access_control":
        return ["LAN"]
    return ["eth0"]


def ports_for(dev):
    ports = []
    for name in _port_names(dev):
        conn = None
        peer = None
        for c in CONNECTIONS:
            if c["a_device"]["id"] == dev["id"] and c["a_port"] in (name, f"{name} (rear)"):
                conn, peer = c, ("b", c)
            elif c["b_device"] and c["b_device"]["id"] == dev["id"] and c["b_port"] in (name, f"{name} (rear)"):
                conn, peer = c, ("a", c)
            if conn:
                break
        if conn:
            side = peer[0]
            if side == "b":
                to_dev, to_port, ext = conn["b_device"], conn["b_port"], conn["b_external"]
            else:
                to_dev, to_port, ext = conn["a_device"], conn["a_port"], None
            status = "down" if to_dev and to_dev["status"] == "faulty" else "up"
            ports.append({"name": name, "status": status, "connection": conn, "to_device": to_dev,
                          "to_port": to_port, "external": ext, "function": conn["function"],
                          "mode": conn["mode"], "vlans": conn["vlans"], "remark": conn["remark"],
                          "speed": "10G" if name.startswith(("Te", "sfp")) else "1G"})
        else:
            disabled = dev["type"] == "switch" and name.endswith(("/47", "/48"))
            ports.append({"name": name, "status": "disabled" if disabled else "empty", "connection": None,
                          "to_device": None, "to_port": None, "external": None, "function": "",
                          "mode": "access" if dev["type"] == "switch" else "—", "vlans": "10" if disabled else "—",
                          "remark": "Shut down — security policy" if disabled else "",
                          "speed": "10G" if name.startswith(("Te", "sfp")) else "1G"})
    return ports


def trace(conn_id):
    """Follow a connection through patch-panel pass-throughs and return the hops."""
    conn = next(c for c in CONNECTIONS if c["id"] == conn_id)
    # Walk backwards to the true start if we're mid-path.
    while conn["a_device"]["type"] == "patch_panel" and conn["a_port"].endswith("(rear)"):
        front = conn["a_port"].replace(" (rear)", "")
        prev = next((c for c in CONNECTIONS if c["b_device"] and c["b_device"]["id"] == conn["a_device"]["id"]
                     and c["b_port"] == front), None)
        if not prev:
            break
        conn = prev
    hops = [{"device": conn["a_device"], "port": conn["a_port"]}]
    cables = []
    while True:
        cables.append(conn)
        if conn["b_external"]:
            hops.append({"device": None, "port": None, "external": conn["b_external"]})
            break
        hops.append({"device": conn["b_device"], "port": conn["b_port"]})
        if conn["b_device"]["type"] != "patch_panel":
            break
        rear = f"{conn['b_port']} (rear)"
        nxt = next((c for c in CONNECTIONS if c["a_device"]["id"] == conn["b_device"]["id"] and c["a_port"] == rear),
                   None)
        if not nxt:
            break
        hops.append({"device": conn["b_device"], "port": rear, "passthrough": True})
        conn = nxt
    return hops, cables


# --------------------------------------------------------------------------
# Monitoring
# --------------------------------------------------------------------------

_M = [
    # id, name, type, target, device, parent_monitor, status, cause
    (1, "Edge router", "ping", "10.10.0.254", 1, None, "up", ""),
    (2, "Core firewall", "ping", "10.10.0.1", 2, 1, "up", ""),
    (3, "Core switch 01", "ping", "10.10.0.2", 4, 2, "up", ""),
    (4, "Core switch 02", "ping", "10.10.0.3", 5, 2, "up", ""),
    (5, "Server switch", "ping", "10.10.0.4", 8, 3, "up", ""),
    (6, "Hypervisor 01", "ping", "10.10.10.5", 9, 5, "up", ""),
    (7, "Hypervisor 02", "ping", "10.10.10.6", 10, 5, "up", ""),
    (8, "Web server 01", "ping", "10.10.10.11", 11, 5, "up", ""),
    (9, "Web server 02", "ping", "10.10.10.12", 12, 5, "up", ""),
    (10, "Database server", "tcp", "10.10.10.21:5432", 13, 5, "maintenance", ""),
    (11, "Storage array", "ping", "10.10.10.40", 14, 5, "up", ""),
    (12, "CCTV switch", "ping", "10.20.0.2", 16, 4, "down", "Timeout — no ICMP reply after 3 checks"),
    (13, "NVR 01", "ping", "10.20.0.10", 18, 12, "unreachable", "Parent CCTV switch is down"),
    (14, "NVR 02", "ping", "10.20.0.11", 19, 12, "unreachable", "Parent CCTV switch is down"),
    (15, "Camera — lobby", "ping", "10.20.1.21", 22, 12, "unreachable", "Parent CCTV switch is down"),
    (16, "Camera — main gate", "ping", "10.20.1.22", 23, 12, "unreachable", "Parent CCTV switch is down"),
    (17, "Access control panel", "tcp", "10.30.0.10:4370", 20, 4, "up", ""),
    (18, "UPS rack 1", "ping", "10.10.0.30", 7, 3, "up", ""),
    (19, "Customer portal", "http", "https://portal.acme.example", 11, 8, "up", ""),
    (20, "Company website", "keyword", "https://www.acme.example", 12, 9, "up", ""),
    (21, "HR portal", "http", "https://hr.acme.example", 11, 8, "down", "HTTP 503 Service Unavailable"),
    (22, "Webmail", "ssl", "mail.acme.example:443", None, None, "up", ""),
    (23, "VPN gateway", "tcp", "129.205.100.134:443", 2, 2, "up", ""),
    (24, "Public DNS — acme.example", "dns", "acme.example A", None, None, "up", ""),
    (25, "Wi-Fi AP floor 1", "ping", "10.40.0.21", 27, 3, "pending", "1 of 3 checks failed"),
    (26, "Camera — car park", "ping", "10.20.1.23", 24, 12, "paused", "Paused — camera under RMA"),
]


def _heartbeats(status, rng, n=60):
    beats = []
    for i in range(n):
        tail = n - i
        if status == "down" and tail <= 7:
            beats.append("down")
        elif status == "unreachable" and tail <= 6:
            beats.append("unreachable")
        elif status == "maintenance" and tail <= 10:
            beats.append("maintenance")
        elif status == "paused" and tail <= 20:
            beats.append("paused")
        elif status == "pending" and tail <= 1:
            beats.append("pending")
        else:
            beats.append("down" if rng.random() < 0.02 else "up")
    return beats


MONITORS = []
for (mid, name, mtype, target, dev, parent, status, cause) in _M:
    r = random.Random(mid)
    base = {"ping": 2, "tcp": 4, "http": 180, "keyword": 220, "ssl": 260, "dns": 35}[mtype]
    series = [max(1, int(base * r.uniform(0.6, 1.6))) for _ in range(48)]
    if status == "down":
        series[-6:] = [None] * 6
    uptime = {"up": r.uniform(99.6, 100), "down": r.uniform(96.0, 98.4), "unreachable": r.uniform(97.0, 98.8),
              "maintenance": r.uniform(98.9, 99.6), "pending": r.uniform(99.2, 99.9),
              "paused": r.uniform(91, 95)}[status]
    MONITORS.append({
        "id": mid, "name": name, "type": mtype, "target": target,
        "device": DEVICES_BY_ID.get(dev), "parent_id": parent, "status": status, "cause": cause,
        "interval": 30 if mtype in ("http", "keyword") else 60, "retries": 3, "timeout": 10,
        "uptime_24h": round(uptime, 2), "uptime_7d": round(min(100, uptime + 0.15), 2),
        "uptime_30d": round(min(100, uptime + 0.3), 2),
        "response_ms": None if status in ("down", "unreachable", "paused") else series[-1],
        "avg_ms": int(sum(s for s in series if s) / len([s for s in series if s])),
        "last_check_s": r.randint(3, 55),
        "since": ago(minutes=r.randint(9, 50)) if status in ("down", "unreachable") else ago(days=r.randint(2, 40)),
        "heartbeats": _heartbeats(status, r),
        "series": series,
        "cert_days": {19: 71, 20: 128, 21: 71, 22: 9, 23: None}.get(mid),
    })
MONITORS_BY_ID = {m["id"]: m for m in MONITORS}
MONITORS_BY_ID[12]["since"] = ago(minutes=23)
for _mid in (13, 14, 15, 16):
    MONITORS_BY_ID[_mid]["since"] = ago(minutes=22)
MONITORS_BY_ID[21]["since"] = ago(minutes=8)

MONITOR_TYPES = {
    "ping": "Ping (ICMP)", "tcp": "TCP port", "http": "HTTP(S)", "keyword": "HTTP(S) keyword",
    "dns": "DNS record", "ssl": "SSL certificate",
}

STATUS_META = {
    "up": {"label": "Up", "color": "up"},
    "down": {"label": "Down", "color": "down"},
    "unreachable": {"label": "Unreachable", "color": "unreach"},
    "maintenance": {"label": "Maintenance", "color": "maint"},
    "pending": {"label": "Pending", "color": "pending"},
    "paused": {"label": "Paused", "color": "paused"},
}


def status_counts():
    counts = {k: 0 for k in STATUS_META}
    for m in MONITORS:
        counts[m["status"]] += 1
    return counts


INCIDENTS = [
    {"id": 118, "monitor": MONITORS_BY_ID[12], "status": "open", "started": ago(minutes=23), "ended": None,
     "duration": "23m", "cause": "Timeout — no ICMP reply after 3 checks", "ack": None, "suppressed": 4},
    {"id": 117, "monitor": MONITORS_BY_ID[21], "status": "open", "started": ago(minutes=8), "ended": None,
     "duration": "8m", "cause": "HTTP 503 Service Unavailable", "ack": "Aisha Bello", "suppressed": 0},
    {"id": 116, "monitor": MONITORS_BY_ID[2], "status": "resolved", "started": ago(hours=19, minutes=12),
     "ended": ago(hours=19), "duration": "12m 40s", "cause": "Timeout — no ICMP reply", "ack": "Musa Ibrahim",
     "suppressed": 14},
    {"id": 115, "monitor": MONITORS_BY_ID[19], "status": "resolved", "started": ago(days=1, hours=6),
     "ended": ago(days=1, hours=5, minutes=56), "duration": "4m 10s", "cause": "Connection refused (port 443)",
     "ack": None, "suppressed": 0},
    {"id": 114, "monitor": MONITORS_BY_ID[25], "status": "resolved", "started": ago(days=2, hours=1),
     "ended": ago(days=2), "duration": "1h 02m", "cause": "Host unreachable", "ack": "Chinedu Okafor",
     "suppressed": 0},
    {"id": 113, "monitor": MONITORS_BY_ID[24], "status": "resolved", "started": ago(days=4, hours=3),
     "ended": ago(days=4, hours=2, minutes=51), "duration": "9m", "cause": "DNS failure — SERVFAIL",
     "ack": None, "suppressed": 0},
    {"id": 112, "monitor": MONITORS_BY_ID[20], "status": "resolved", "started": ago(days=6, hours=8),
     "ended": ago(days=6, hours=7, minutes=58), "duration": "2m 30s", "cause": "Keyword \"Welcome\" not found",
     "ack": None, "suppressed": 0},
]

MAINTENANCE = [
    {"id": 7, "name": "DB server patching", "state": "active", "start": ago(minutes=35), "end": ahead(minutes=85),
     "recurrence": "One-time", "targets": ["SRV-DB-01"], "children": False,
     "reason": "Monthly OS and PostgreSQL security updates", "by": "Chinedu Okafor"},
    {"id": 8, "name": "Core switch firmware", "state": "scheduled", "start": ahead(days=2, hours=4),
     "end": ahead(days=2, hours=6), "recurrence": "One-time", "targets": ["SW-CORE-01", "SW-CORE-02"],
     "children": True, "reason": "IOS-XE 17.12.4 → 17.12.5", "by": "Musa Ibrahim"},
    {"id": 6, "name": "Weekly NVR reboot", "state": "scheduled", "start": ahead(days=1, hours=5),
     "end": ahead(days=1, hours=5, minutes=20), "recurrence": "Weekly — Sun 02:00", "targets": ["NVR-01", "NVR-02"],
     "children": False, "reason": "Vendor-recommended reboot", "by": "Aisha Bello"},
    {"id": 5, "name": "UPS battery replacement", "state": "completed", "start": ago(days=5, hours=6),
     "end": ago(days=5, hours=4), "recurrence": "One-time", "targets": ["UPS-R1"], "children": True,
     "reason": "Battery replacement — RBC55", "by": "Musa Ibrahim"},
]

ALERTS = [
    {"time": ago(minutes=8), "event": "down", "monitor": "HR portal", "text": "HTTP 503 Service Unavailable",
     "recipients": 3, "status": "sent"},
    {"time": ago(minutes=22), "event": "down", "monitor": "CCTV switch",
     "text": "Timeout — 4 devices unreachable behind it", "recipients": 3, "status": "sent"},
    {"time": ago(hours=3), "event": "ssl", "monitor": "Webmail", "text": "Certificate expires in 9 days",
     "recipients": 3, "status": "sent"},
    {"time": ago(hours=19), "event": "up", "monitor": "Core firewall", "text": "Back up — was down 12m 40s",
     "recipients": 3, "status": "sent"},
    {"time": ago(hours=19, minutes=12), "event": "down", "monitor": "Core firewall",
     "text": "Timeout — 14 devices unreachable behind it", "recipients": 3, "status": "delayed"},
    {"time": ago(days=1, hours=3), "event": "vault", "monitor": "Vault",
     "text": "Chinedu Okafor locked out after 5 failed reveal attempts", "recipients": 1, "status": "sent"},
]

# --------------------------------------------------------------------------
# IPAM
# --------------------------------------------------------------------------

ISPS = [
    {"id": 1, "name": "FibreLink Networks", "account": "FL-0087231", "phone": "+234 800 000 1100",
     "email": "noc@fibrelink.example", "manager": "Tunde Akin", "portal": "https://my.fibrelink.example",
     "circuits": [{"id": "FL-ABJ-22817", "type": "Fibre (GPON)", "bandwidth": "200 Mbps / 200 Mbps",
                   "contract_end": ahead(days=214).date(), "contract_days": 214, "cost": "₦ 1,450,000 / month",
                   "termination": "RTR-EDGE-01 · sfp-sfpplus1", "status": "up"}]},
    {"id": 2, "name": "SkyWave Wireless", "account": "SW-55102", "phone": "+234 800 000 2200",
     "email": "support@skywave.example", "manager": "Ngozi Eze", "portal": "https://portal.skywave.example",
     "circuits": [{"id": "SW-00931", "type": "Point-to-point radio", "bandwidth": "50 Mbps / 50 Mbps",
                   "contract_end": ahead(days=33).date(), "contract_days": 33, "cost": "₦ 380,000 / month",
                   "termination": "RTR-EDGE-01 · ether2", "status": "up"}]},
]

_P = [
    # id, cidr, kind, isp, vlan, description, gateway
    (1, "129.205.100.128/28", "public", "FibreLink Networks", 99, "Primary public block", "129.205.100.129"),
    (2, "203.0.113.8/29", "public", "SkyWave Wireless", None, "Backup public block", "203.0.113.9"),
    (3, "10.10.0.0/24", "private", None, 10, "Management", "10.10.0.1"),
    (4, "10.10.10.0/24", "private", None, 20, "Servers", "10.10.10.1"),
    (5, "10.20.0.0/22", "private", None, 30, "CCTV", "10.20.0.1"),
    (6, "10.30.0.0/24", "private", None, 40, "Access control", "10.30.0.1"),
    (7, "10.40.0.0/23", "private", None, 50, "Staff Wi-Fi", "10.40.0.1"),
    (8, "192.168.100.0/24", "private", None, 60, "Guest Wi-Fi", "192.168.100.1"),
]

# Stored assignments (only used/reserved IPs are stored — see PLAN.md §6.4)
IP_ASSIGNMENTS = {
    "129.205.100.129": {"status": "reserved", "service": "ISP gateway", "device": None, "dns": "",
                        "nat": "", "desc": "FibreLink side of the handoff"},
    "129.205.100.130": {"status": "used", "service": "Firewall WAN", "device": "FW-CORE-01", "dns": "fw.acme.example",
                        "nat": "", "desc": "Default outbound NAT"},
    "129.205.100.131": {"status": "used", "service": "Customer portal", "device": "SRV-WEB-01",
                        "dns": "portal.acme.example", "nat": "→ 10.10.10.11 : 443, 80", "desc": ""},
    "129.205.100.132": {"status": "used", "service": "Company website", "device": "SRV-WEB-02",
                        "dns": "www.acme.example", "nat": "→ 10.10.10.12 : 443, 80", "desc": ""},
    "129.205.100.133": {"status": "used", "service": "Mail server", "device": "SRV-HV-01",
                        "dns": "mail.acme.example", "nat": "→ 10.10.10.30 : 25, 443, 993", "desc": "VM on HV-01"},
    "129.205.100.134": {"status": "used", "service": "VPN gateway", "device": "FW-CORE-01",
                        "dns": "vpn.acme.example", "nat": "", "desc": "FortiGate SSL-VPN"},
    "129.205.100.135": {"status": "reserved", "service": "HR portal", "device": "SRV-WEB-01",
                        "dns": "hr.acme.example", "nat": "→ 10.10.10.11 : 8443", "desc": "Go-live next month"},
    "203.0.113.9": {"status": "reserved", "service": "ISP gateway", "device": None, "dns": "", "nat": "",
                    "desc": "SkyWave side"},
    "203.0.113.10": {"status": "used", "service": "Backup WAN", "device": "RTR-EDGE-01", "dns": "", "nat": "",
                     "desc": "Failover outbound"},
}


def _prefix(pid, cidr, kind, isp, vlan, desc, gw):
    net = ipaddress.ip_network(cidr)
    hosts = list(net.hosts())
    usable = len(hosts)
    used = sum(1 for a in IP_ASSIGNMENTS if ipaddress.ip_address(a) in net)
    if kind == "private":
        used = {3: 14, 4: 11, 5: 37, 6: 4, 7: 212, 8: 97}[pid]
    return {
        "id": pid, "cidr": cidr, "kind": kind, "isp": isp, "vlan": vlan, "desc": desc, "gateway": gw,
        "network": str(net.network_address), "broadcast": str(net.broadcast_address),
        "netmask": str(net.netmask), "wildcard": str(net.hostmask), "prefixlen": net.prefixlen,
        "first": str(hosts[0]), "last": str(hosts[-1]), "total": net.num_addresses, "usable": usable,
        "used": used, "pct": round(used * 100 / usable),
    }


PREFIXES = [_prefix(*p) for p in _P]
PREFIXES_BY_ID = {p["id"]: p for p in PREFIXES}


def prefix_ips(prefix):
    net = ipaddress.ip_network(prefix["cidr"])
    assigned = dict(IP_ASSIGNMENTS)
    for d in DEVICES:
        if d["mgmt_ip"] and ipaddress.ip_address(d["mgmt_ip"]) in net:
            assigned[d["mgmt_ip"]] = {"status": "used", "service": f"{d['type_label']} management",
                                      "device": d["name"], "dns": "", "nat": "", "desc": d["model"]}
    if prefix["gateway"] not in assigned:
        assigned[prefix["gateway"]] = {"status": "reserved", "service": "Default gateway", "device": None,
                                       "dns": "", "nat": "", "desc": ""}
    rows = []
    for addr in net:
        a = str(addr)
        if addr == net.network_address:
            rows.append({"ip": a, "role": "network", "status": "system", "service": "Network address"})
        elif addr == net.broadcast_address:
            rows.append({"ip": a, "role": "broadcast", "status": "system", "service": "Broadcast address"})
        elif a in assigned:
            rows.append({"ip": a, "role": "gateway" if a == prefix["gateway"] else "host", **assigned[a]})
        else:
            rows.append({"ip": a, "role": "host", "status": "free", "service": "", "device": None, "dns": "",
                         "nat": "", "desc": ""})
    return rows


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------

AUDIT = [
    {"id": 9841, "time": ago(minutes=4), "actor": "Musa Ibrahim", "action": "login", "object": "—",
     "ip": "10.10.0.101", "changes": {}},
    {"id": 9840, "time": ago(minutes=8), "actor": "System", "action": "incident_open", "object": "Monitor · HR portal",
     "ip": "—", "changes": {"status": ["up", "down"]}},
    {"id": 9839, "time": ago(minutes=22), "actor": "System", "action": "incident_open",
     "object": "Monitor · CCTV switch", "ip": "—", "changes": {"status": ["up", "down"]}},
    {"id": 9838, "time": ago(minutes=35), "actor": "Chinedu Okafor", "action": "reveal",
     "object": "Credential · PostgreSQL superuser", "ip": "10.10.0.112", "changes": {}},
    {"id": 9837, "time": ago(minutes=36), "actor": "Chinedu Okafor", "action": "maintenance_start",
     "object": "Maintenance · DB server patching", "ip": "10.10.0.112", "changes": {}},
    {"id": 9836, "time": ago(minutes=40), "actor": "Musa Ibrahim", "action": "copy",
     "object": "Credential · Core switch 01 — enable", "ip": "10.10.0.101", "changes": {}},
    {"id": 9835, "time": ago(hours=1, minutes=10), "actor": "Aisha Bello", "action": "update",
     "object": "Port · SW-CORE-01 Gi1/0/10", "ip": "10.10.0.108",
     "changes": {"vlans": ["10,20,30,40,50", "10,20,30,40,50,60"], "remark": ["", "LACP member 1"]}},
    {"id": 9834, "time": ago(hours=1, minutes=12), "actor": "Aisha Bello", "action": "create",
     "object": "VLAN · 60 Guest Wi-Fi", "ip": "10.10.0.108", "changes": {"vid": [None, 60], "name": [None, "Guest Wi-Fi"]}},
    {"id": 9833, "time": ago(hours=2), "actor": "Aisha Bello", "action": "reveal",
     "object": "Credential · FortiGate admin", "ip": "10.10.0.108", "changes": {}},
    {"id": 9832, "time": ago(hours=3), "actor": "Musa Ibrahim", "action": "update",
     "object": "IP · 129.205.100.135", "ip": "10.10.0.101",
     "changes": {"status": ["free", "reserved"], "service": ["", "HR portal"]}},
    {"id": 9831, "time": ago(hours=5), "actor": "Grace Adeyemi", "action": "export", "object": "Devices (CSV, 30 rows)",
     "ip": "10.10.0.120", "changes": {}},
    {"id": 9830, "time": ago(hours=7), "actor": "Musa Ibrahim", "action": "update", "object": "Credential · Web 01 — root",
     "ip": "10.10.0.101", "changes": {"password": ["•••", "••• changed"]}},
    {"id": 9829, "time": ago(days=1, hours=3), "actor": "Chinedu Okafor", "action": "vault_locked",
     "object": "User · Chinedu Okafor", "ip": "10.10.0.112", "changes": {"failed_attempts": [4, 5]}},
    {"id": 9828, "time": ago(days=1, hours=3, minutes=1), "actor": "Chinedu Okafor", "action": "reveal_failed",
     "object": "Credential · HV-01 iLO", "ip": "10.10.0.112", "changes": {}},
    {"id": 9827, "time": ago(days=1, hours=9), "actor": "Unknown", "action": "login_failed",
     "object": "username: admin", "ip": "10.40.0.77", "changes": {}},
    {"id": 9826, "time": ago(days=2), "actor": "Musa Ibrahim", "action": "create", "object": "Device · AP-RECEP-01",
     "ip": "10.10.0.101", "changes": {"name": [None, "AP-RECEP-01"], "model": [None, "U6 Lite"]}},
    {"id": 9825, "time": ago(days=2, hours=1), "actor": "Musa Ibrahim", "action": "user_role_change",
     "object": "User · Grace Adeyemi", "ip": "10.10.0.101", "changes": {"role": ["Engineer", "Viewer"]}},
    {"id": 9824, "time": ago(days=3), "actor": "Musa Ibrahim", "action": "import",
     "object": "Connections (XLSX, 28 rows)", "ip": "10.10.0.101", "changes": {}},
    {"id": 9823, "time": ago(days=4), "actor": "Musa Ibrahim", "action": "settings_change",
     "object": "Settings · Alerts", "ip": "10.10.0.101", "changes": {"reminder_interval": ["30 min", "60 min"]}},
    {"id": 9822, "time": ago(days=4, hours=2), "actor": "Musa Ibrahim", "action": "delete",
     "object": "Monitor · Old intranet", "ip": "10.10.0.101", "changes": {}},
]

AUDIT_ACTION_META = {
    "login": ("Login", "info"), "login_failed": ("Login failed", "down"), "logout": ("Logout", "muted"),
    "create": ("Created", "up"), "update": ("Updated", "info"), "delete": ("Deleted", "down"),
    "reveal": ("Password revealed", "warn"), "copy": ("Password copied", "warn"),
    "reveal_failed": ("Reveal failed", "down"), "vault_locked": ("Vault locked", "down"),
    "export": ("Export", "muted"), "import": ("Import", "muted"), "settings_change": ("Settings changed", "info"),
    "user_role_change": ("Role changed", "warn"), "maintenance_start": ("Maintenance started", "maint"),
    "incident_open": ("Incident opened", "down"),
}

