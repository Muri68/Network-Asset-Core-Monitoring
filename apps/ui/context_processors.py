from django.urls import reverse

from . import fake_data as fd

NAV = [
    ("Monitoring", [
        ("dashboard", "Dashboard", "layout-dashboard", "ui:dashboard"),
        ("monitors", "Monitors", "activity", "ui:monitors"),
        ("incidents", "Incidents", "siren", "ui:incidents"),
        ("maintenance", "Maintenance", "wrench", "ui:maintenance"),
    ]),
    ("Infrastructure", [
        ("devices", "Devices", "server", "ui:devices"),
        ("racks", "Racks", "layers", "ui:racks"),
        ("connections", "Connections", "cable", "ui:connections"),
        ("vlans", "VLANs", "hash", "ui:vlans"),
    ]),
    ("IP addressing", [
        ("prefixes", "Prefixes & IPs", "globe", "ui:prefixes"),
        ("isps", "ISPs & circuits", "building-2", "ui:isps"),
        ("calculator", "Subnet calculator", "grid-3x3", "ui:calculator"),
    ]),
    ("Security", [
        ("vault", "Password vault", "folder-lock", "ui:vault"),
        ("audit", "Audit log", "scroll-text", "ui:audit"),
    ]),
    ("Administration", [
        ("users", "Users & roles", "users", "ui:users"),
        ("data", "Import / export", "file-spreadsheet", "ui:data"),
        ("settings", "Settings", "settings", "ui:settings"),
    ]),
]


def _search_index():
    items = [{"kind": "Page", "label": label, "sub": section, "url": reverse(url), "icon": icon}
             for section, links in NAV for _key, label, icon, url in links]
    items += [{"kind": "Device", "label": d["name"], "sub": f"{d['model']} · {d['mgmt_ip'] or 'no IP'}",
               "url": reverse("ui:device_detail", args=[d["id"]]), "icon": d["icon"]} for d in fd.DEVICES]
    items += [{"kind": "Monitor", "label": m["name"], "sub": m["target"],
               "url": reverse("ui:monitor_detail", args=[m["id"]]), "icon": "activity"} for m in fd.MONITORS]
    items += [{"kind": "Prefix", "label": p["cidr"], "sub": p["desc"],
               "url": reverse("ui:prefix_detail", args=[p["id"]]), "icon": "globe"} for p in fd.PREFIXES]
    items += [{"kind": "IP", "label": ip, "sub": a["service"],
               "url": reverse("ui:prefixes"), "icon": "map-pin"} for ip, a in fd.IP_ASSIGNMENTS.items()]
    items += [{"kind": "Credential", "label": c["name"], "sub": c["username"],
               "url": reverse("ui:credential_detail", args=[c["id"]]), "icon": "key-round"} for c in fd.CREDENTIALS]
    return items


def shell(request):
    counts = fd.status_counts()
    open_incidents = sum(1 for i in fd.INCIDENTS if i["status"] == "open")
    return {
        "nav": NAV,
        "org_name": fd.ORG_NAME,
        "me": fd.CURRENT_USER,
        "nav_badges": {"monitors": counts["down"], "incidents": open_incidents},
        "shell_counts": counts,
        "shell_alerts": fd.ALERTS[:5],
        "search_index": _search_index(),
    }
