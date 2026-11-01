"""Every page × every role (PLAN.md §9 "Permissions")."""

import pytest
from django.urls import reverse

from apps.accounts import roles
from tests.factories import make_user

# (url name, args, capability needed). Every GET page in the app must be listed here:
# test_every_url_is_covered fails if a new page is added without an entry.
PAGES = [
    ("ui:dashboard", [], "view"),
    ("ui:dashboard_live", [], "view"),
    ("monitoring:monitors", [], "view"),
    ("monitoring:monitor_detail", ["monitor"], "view"),
    ("monitoring:monitor_new", [], "edit"),
    ("monitoring:monitor_edit", ["monitor"], "edit"),
    ("monitoring:incidents", [], "view"),
    ("monitoring:maintenance", [], "view"),
    ("monitoring:maintenance_new", [], "edit"),
    ("monitoring:maintenance_edit", ["window"], "edit"),
    ("inventory:devices", [], "view"),
    ("inventory:device_detail", ["device"], "view"),
    ("inventory:device_new", [], "edit"),
    ("inventory:device_edit", ["device"], "edit"),
    ("inventory:racks", [], "view"),
    ("inventory:rack_detail", ["rack"], "view"),
    ("inventory:rack_new", [], "edit"),
    ("inventory:rack_edit", ["rack"], "edit"),
    ("inventory:rack_check", [], "edit"),
    ("inventory:location_new", [], "edit"),
    ("inventory:location_edit", ["location"], "edit"),
    ("dcim:connections", [], "view"),
    ("dcim:connection_trace", ["connection"], "view"),
    ("dcim:connection_new", [], "edit"),
    ("dcim:connection_edit", ["connection"], "edit"),
    ("dcim:vlans", [], "view"),
    ("dcim:vlan_new", [], "edit"),
    ("dcim:vlan_edit", ["vlan"], "edit"),
    ("dcim:port_edit", ["port"], "edit"),
    ("dcim:port_options", [], "view"),
    ("dcim:pattern_preview", [], "view"),
    ("inventory:device_models", [], "view"),
    ("inventory:device_model_edit", ["model"], "edit"),
    ("ipam:prefixes", [], "view"),
    ("ipam:prefix_detail", ["prefix"], "view"),
    ("ipam:prefix_new", [], "edit"),
    ("ipam:prefix_edit", ["prefix"], "edit"),
    ("ipam:ip_new", [], "edit"),
    ("ipam:ip_edit", ["ip"], "edit"),
    ("ipam:isps", [], "view"),
    ("ipam:isp_new", [], "edit"),
    ("ipam:isp_edit", ["isp"], "edit"),
    ("ipam:circuit_new", [], "edit"),
    ("ipam:circuit_edit", ["circuit"], "edit"),
    ("ipam:calculator", [], "view"),
    ("ui:vault", [], "vault"),
    ("ui:credential_detail", [1], "vault"),
    ("ui:reveal_log", [], "vault"),
    ("audit:log", [], "audit"),
    ("audit:export", [], "audit"),
    ("ui:data", [], "export"),
    ("ui:settings", [], "settings"),
    ("accounts:users", [], "manage_users"),
    ("accounts:profile", [], "view"),
]

POST_ONLY = {
    "accounts:logout", "accounts:profile_backup_codes", "accounts:profile_password",
    "accounts:profile_reveal_code", "accounts:user_action", "accounts:user_create", "dcim:connection_delete",
    "dcim:port_delete", "dcim:ports_apply_template", "dcim:ports_bulk_add", "dcim:vlan_delete",
    "inventory:device_delete", "inventory:device_retire", "inventory:location_delete", "inventory:rack_delete",
    "ipam:circuit_delete", "ipam:ip_delete", "ipam:isp_delete", "ipam:prefix_delete", "monitoring:incident_ack",
    "monitoring:maintenance_delete", "monitoring:maintenance_end", "monitoring:monitor_action",
    "monitoring:monitor_delete", "ui:credential_reveal", "ui:import_preview",
}
PUBLIC = {"accounts:login", "accounts:login_password", "accounts:login_2fa_setup", "accounts:login_2fa"}
SPECIAL = {"accounts:backup_codes", "accounts:reveal_code_setup", "accounts:user_edit"}


def test_every_url_is_covered():
    from django.urls import get_resolver

    names = set()
    for ns in ("ui", "accounts", "audit", "inventory", "dcim", "ipam", "monitoring"):
        resolver = get_resolver().namespace_dict[ns][1]
        names |= {f"{ns}:{n}" for n in resolver.reverse_dict if isinstance(n, str)}
    covered = {name for name, _, _ in PAGES} | POST_ONLY | PUBLIC | SPECIAL
    assert names - covered == set(), "Add new pages to PAGES in test_permissions.py"


@pytest.fixture
def objects(db):
    from apps.dcim.models import VLAN, Connection
    from apps.dcim.services import create_ports
    from tests.inventory.helpers import make_device

    device = make_device()
    other = make_device(rack=None)
    (a_port,), _ = create_ports(device, ["port1"])
    (b_port,), _ = create_ports(other, ["Gi1/0/10"])
    connection = Connection.objects.create(a_port=a_port, b_port=b_port, function="Uplink")
    vlan = VLAN.objects.create(vid=10, name="Management")
    from apps.ipam.models import ISP, Circuit, IPAddress, Prefix

    prefix = Prefix.objects.create(cidr="129.205.100.128/28", kind="public")
    ip = IPAddress.objects.create(address="129.205.100.131", device=device)
    isp = ISP.objects.create(name="FibreLink")
    circuit = Circuit.objects.create(isp=isp, circuit_id="FL-1")
    import datetime

    from django.utils import timezone

    from apps.monitoring.models import MaintenanceWindow, Monitor

    monitor = Monitor.objects.create(name="Core firewall", type="ping", target="10.10.0.1", device=device)
    window = MaintenanceWindow.objects.create(name="Patch", starts_at=timezone.now(),
                                              ends_at=timezone.now() + datetime.timedelta(hours=1))
    window.devices.add(device)
    return {"device": device.pk, "rack": device.rack.pk, "location": device.location.pk, "port": a_port.pk,
            "connection": connection.pk, "vlan": vlan.pk, "model": device.device_model.pk, "prefix": prefix.pk,
            "ip": ip.pk, "isp": isp.pk, "circuit": circuit.pk, "monitor": monitor.pk, "window": window.pk}


def resolve(args, objects):
    return [objects[a] if isinstance(a, str) else a for a in args]


@pytest.mark.django_db
@pytest.mark.parametrize("name,args,cap", PAGES)
def test_anonymous_is_sent_to_login(client, objects, name, args, cap):
    response = client.get(reverse(name, args=resolve(args, objects)))
    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login"))


@pytest.mark.django_db
@pytest.mark.parametrize("role", roles.ROLES)
@pytest.mark.parametrize("name,args,cap", PAGES)
def test_role_access(as_user, objects, role, name, args, cap):
    client = as_user(make_user(role))
    response = client.get(reverse(name, args=resolve(args, objects)))
    if role in roles.CAPABILITIES[cap]:
        assert response.status_code == 200, f"{role} should reach {name}"
    else:
        assert response.status_code == 403, f"{role} must not reach {name}"


@pytest.mark.django_db
@pytest.mark.parametrize("role,allowed", [(roles.ADMIN, True), (roles.ENGINEER, True), (roles.VIEWER, False)])
def test_vault_reveal_post(as_user, role, allowed):
    client = as_user(make_user(role))
    response = client.post(reverse("ui:credential_reveal", args=[1]), {"code": "000000"})
    assert response.status_code == (200 if allowed else 403)


@pytest.mark.django_db
@pytest.mark.parametrize("role,allowed", [(roles.ADMIN, True), (roles.ENGINEER, False), (roles.VIEWER, False)])
def test_import_is_admin_only(as_user, role, allowed):
    client = as_user(make_user(role))
    response = client.post(reverse("ui:import_preview"), {"resource": "devices"})
    assert response.status_code == (200 if allowed else 403)


@pytest.mark.django_db
def test_viewer_sees_no_admin_or_vault_links(as_user, viewer):
    html = as_user(viewer).get(reverse("ui:dashboard")).content.decode()
    for name in ("ui:vault", "audit:log", "ui:settings", "accounts:users", "ui:data", "monitoring:monitor_new"):
        assert f'href="{reverse(name)}"' not in html, name
    assert '"kind": "Credential"' not in html  # search palette doesn't list credentials


@pytest.mark.django_db
def test_engineer_nav(as_user, engineer):
    html = as_user(engineer).get(reverse("ui:dashboard")).content.decode()
    assert f'href="{reverse("ui:vault")}"' in html
    assert f'href="{reverse("monitoring:monitor_new")}"' in html
    for name in ("audit:log", "ui:settings", "accounts:users"):
        assert f'href="{reverse(name)}"' not in html, name


@pytest.mark.django_db
def test_device_page_hides_edit_for_viewer(as_user, viewer, objects):
    html = as_user(viewer).get(reverse("inventory:device_detail", args=[objects["device"]])).content.decode()
    assert reverse("inventory:device_edit", args=[objects["device"]]) not in html
    assert "Retire device" not in html and "Delete permanently" not in html
    assert "Credentials" not in html  # vault tab hidden from viewers


@pytest.mark.django_db
def test_connections_page_hides_edit_for_viewer(as_user, viewer, objects):
    html = as_user(viewer).get(reverse("dcim:connections")).content.decode()
    assert "Add connection" not in html and reverse("dcim:connection_edit", args=[objects["connection"]]) not in html
    html = as_user(viewer).get(reverse("inventory:device_detail", args=[objects["device"]])).content.decode()
    assert "Add ports" not in html and "Trace" in html
