from django.urls import reverse

from apps.accounts.roles import can
from apps.inventory.models import Device

from . import fake_data as fd

# (key, label, icon, url name, capability needed to see the link)
NAV = [
    ("Monitoring", [
        ("dashboard", "Dashboard", "layout-dashboard", "ui:dashboard", "view"),
        ("monitors", "Monitors", "activity", "ui:monitors", "view"),
        ("incidents", "Incidents", "siren", "ui:incidents", "view"),
        ("maintenance", "Maintenance", "wrench", "ui:maintenance", "view"),
    ]),
    ("Infrastructure", [
        ("devices", "Devices", "server", "inventory:devices", "view"),
        ("racks", "Racks", "layers", "inventory:racks", "view"),
        ("connections", "Connections", "cable", "ui:connections", "view"),
        ("vlans", "VLANs", "hash", "ui:vlans", "view"),
    ]),
    ("IP addressing", [
        ("prefixes", "Prefixes & IPs", "globe", "ui:prefixes", "view"),
        ("isps", "ISPs & circuits", "building-2", "ui:isps", "view"),
        ("calculator", "Subnet calculator", "grid-3x3", "ui:calculator", "view"),
    ]),
    ("Security", [
        ("vault", "Password vault", "folder-lock", "ui:vault", "vault"),
        ("audit", "Audit log", "scroll-text", "audit:log", "audit"),
    ]),
    ("Administration", [
        ("users", "Users & roles", "users", "accounts:users", "manage_users"),
        ("data", "Import / export", "file-spreadsheet", "ui:data", "export"),
        ("settings", "Settings", "settings", "ui:settings", "settings"),
    ]),
]


def _nav_for(user):
    sections = []
    for section, links in NAV:
        visible = [(key, label, icon, url) for key, label, icon, url, cap in links if can(user, cap)]
        if visible:
            sections.append((section, visible))
    return sections


def _search_index(user, nav):
    items = [{"kind": "Page", "label": label, "sub": section, "url": reverse(url), "icon": icon}
             for section, links in nav for _key, label, icon, url in links]
    devices = Device.objects.exclude(status=Device.Status.RETIRED).select_related("device_model")
    items += [{"kind": "Device", "label": d.name, "sub": f"{d.device_model.name} · {d.mgmt_ip or 'no IP'}",
               "url": d.get_absolute_url(), "icon": d.icon} for d in devices]
    items += [{"kind": "Monitor", "label": m["name"], "sub": m["target"],
               "url": reverse("ui:monitor_detail", args=[m["id"]]), "icon": "activity"} for m in fd.MONITORS]
    items += [{"kind": "Prefix", "label": p["cidr"], "sub": p["desc"],
               "url": reverse("ui:prefix_detail", args=[p["id"]]), "icon": "globe"} for p in fd.PREFIXES]
    items += [{"kind": "IP", "label": ip, "sub": a["service"],
               "url": reverse("ui:prefixes"), "icon": "map-pin"} for ip, a in fd.IP_ASSIGNMENTS.items()]
    if can(user, "vault"):
        items += [{"kind": "Credential", "label": c["name"], "sub": c["username"],
                   "url": reverse("ui:credential_detail", args=[c["id"]]), "icon": "key-round"}
                  for c in fd.CREDENTIALS]
    return items


def shell(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"org_name": fd.ORG_NAME}
    counts = fd.status_counts()
    open_incidents = sum(1 for i in fd.INCIDENTS if i["status"] == "open")
    nav = _nav_for(user)
    return {
        "nav": nav,
        "org_name": fd.ORG_NAME,
        "me": user,
        "nav_badges": {"monitors": counts["down"], "incidents": open_incidents},
        "shell_counts": counts,
        "shell_alerts": fd.ALERTS[:5],
        "search_index": _search_index(user, nav),
    }
