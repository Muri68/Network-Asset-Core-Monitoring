from django.urls import reverse

from apps.accounts.roles import can
from apps.inventory.models import Device
from apps.ipam.models import IPAddress, Prefix
from apps.monitoring import stats as monitor_stats
from apps.monitoring.models import Incident, Monitor, MonitorHeartbeat

from . import fake_data as fd

# (key, label, icon, url name, capability needed to see the link)
NAV = [
    ("Monitoring", [
        ("dashboard", "Dashboard", "layout-dashboard", "ui:dashboard", "view"),
        ("monitors", "Monitors", "activity", "monitoring:monitors", "view"),
        ("incidents", "Incidents", "siren", "monitoring:incidents", "view"),
        ("maintenance", "Maintenance", "wrench", "monitoring:maintenance", "view"),
    ]),
    ("Infrastructure", [
        ("devices", "Devices", "server", "inventory:devices", "view"),
        ("racks", "Racks", "layers", "inventory:racks", "view"),
        ("models", "Device models", "box", "inventory:device_models", "view"),
        ("connections", "Connections", "cable", "dcim:connections", "view"),
        ("vlans", "VLANs", "hash", "dcim:vlans", "view"),
    ]),
    ("IP addressing", [
        ("prefixes", "Prefixes & IPs", "globe", "ipam:prefixes", "view"),
        ("isps", "ISPs & circuits", "building-2", "ipam:isps", "view"),
        ("calculator", "Subnet calculator", "grid-3x3", "ipam:calculator", "view"),
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
    items += [{"kind": "Monitor", "label": m.name, "sub": m.target, "url": m.get_absolute_url(), "icon": "activity"}
              for m in Monitor.objects.only("name", "target")]
    items += [{"kind": "Prefix", "label": p.cidr, "sub": p.description or p.get_kind_display(),
               "url": p.get_absolute_url(), "icon": "globe"} for p in Prefix.objects.all()]
    items += [{"kind": "IP", "label": ip.address, "sub": ip.service or (ip.device.name if ip.device else ""),
               "url": ip.prefix.get_absolute_url() if ip.prefix else reverse("ipam:prefixes"), "icon": "map-pin"}
              for ip in IPAddress.objects.select_related("device", "prefix")]
    if can(user, "vault"):
        items += [{"kind": "Credential", "label": c["name"], "sub": c["username"],
                   "url": reverse("ui:credential_detail", args=[c["id"]]), "icon": "key-round"}
                  for c in fd.CREDENTIALS]
    return items


def shell(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"org_name": fd.ORG_NAME}
    counts = monitor_stats.status_counts()
    open_incidents = Incident.objects.filter(ended_at__isnull=True).count()
    nav = _nav_for(user)
    return {
        "nav": nav,
        "org_name": fd.ORG_NAME,
        "me": user,
        "nav_badges": {"monitors": counts["down"], "incidents": open_incidents},
        "shell_counts": counts,
        "shell_events": Incident.objects.select_related("monitor").order_by("-started_at")[:6],
        "checker": MonitorHeartbeat.current(),
        "search_index": _search_index(user, nav),
    }
