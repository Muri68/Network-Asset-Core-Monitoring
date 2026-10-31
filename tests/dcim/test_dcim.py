"""Ports, VLANs, connections and cable tracing (PLAN.md Phase 3)."""

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import DatabaseError, IntegrityError, transaction
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.dcim.models import VLAN, Connection, ExternalEndpoint, Port, trace
from apps.dcim.patterns import PatternError, expand, expand_lines, natural_key
from apps.dcim.services import create_ports, set_port_vlans
from apps.inventory.models import Device
from tests.inventory.helpers import make_device, make_location, make_model

pytestmark = pytest.mark.django_db


def port(device, name):
    return device.ports.get(name=name)


@pytest.fixture
def fw():
    device = make_device("FW-CORE-01", rack=None, device_type="firewall")
    create_ports(device, expand_lines("wan[1-2]\nport[1-4]"))
    return device


@pytest.fixture
def sw():
    device = make_device("SW-CORE-01", rack=None)
    create_ports(device, expand("Gi1/0/[1-12]"))
    return device


@pytest.fixture
def pp():
    device = make_device("PP-R1-01", rack=None, device_type="patch_panel")
    create_ports(device, expand("[1-4]"), pair_rear=True)
    return device


# --------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------


class TestPatterns:
    def test_ranges_lists_and_combinations(self):
        assert len(expand("GigabitEthernet1/0/[1-48]")) == 48
        assert expand("Gi[1-2]/0/[1-2]") == ["Gi1/0/1", "Gi1/0/2", "Gi2/0/1", "Gi2/0/2"]
        assert expand("port[1,3,5-6]") == ["port1", "port3", "port5", "port6"]
        assert expand("mgmt") == ["mgmt"]

    def test_lines_drop_duplicates(self):
        assert expand_lines("wan[1-2]\n\nmgmt\nWAN1") == ["wan1", "wan2", "mgmt"]

    @pytest.mark.parametrize("bad", ["Gi[48-1]", "Gi[1-48", "Gi[a-b]", "x[1-2000]", "[1-40]/[1-40]"])
    def test_bad_patterns(self, bad):
        with pytest.raises(PatternError):
            expand_lines(bad)

    def test_natural_sort(self):
        assert sorted(["Gi1/0/10", "Gi1/0/9", "Gi1/0/1"], key=natural_key) == ["Gi1/0/1", "Gi1/0/9", "Gi1/0/10"]


# --------------------------------------------------------------------------
# Ports
# --------------------------------------------------------------------------


class TestPorts:
    def test_create_skips_existing_and_sorts_naturally(self, sw):
        created, skipped = create_ports(sw, expand("Gi1/0/[10-14]"))
        assert [p.name for p in created] == ["Gi1/0/13", "Gi1/0/14"] and len(skipped) == 3
        assert [p.name for p in sw.ports.all()][-6:] == [f"Gi1/0/{n}" for n in range(9, 15)]

    def test_port_names_unique_per_device_ignoring_case(self, sw):
        with pytest.raises(ValidationError, match="already has a port"):
            Port(device=sw, name="GI1/0/1").full_clean()

    def test_patch_panel_ports_are_paired(self, pp):
        front = port(pp, "1")
        assert front.type == "front" and front.paired_port.name == "1 (rear)" and front.paired_port.type == "rear"
        assert pp.ports.count() == 8

    def test_bulk_add_view(self, as_user, engineer, sw):
        response = as_user(engineer).post(reverse("dcim:ports_bulk_add", args=[sw.pk]),
                                          {"patterns": "Te1/1/[1-4]", "type": "sfp", "speed": "10G", "mode": "trunk"})
        assert response.url.endswith("?tab=ports")
        assert sw.ports.filter(name__startswith="Te", speed="10G", type="sfp").count() == 4

    def test_bulk_add_refused_for_viewer(self, as_user, viewer, sw):
        assert as_user(viewer).post(reverse("dcim:ports_bulk_add", args=[sw.pk]),
                                    {"patterns": "Te1/1/[1-4]", "type": "sfp"}).status_code == 403

    def test_model_template_applies_on_new_device(self, as_user, engineer):
        make_model("Cisco", "CBS350-24FP").__class__.objects.filter(name="CBS350-24FP").update(
            port_template="Gi[1-24]\nGi[25-26]")
        location = make_location()
        as_user(engineer).post(reverse("inventory:device_new"), {
            "name": "SW-CCTV-01", "device_type": "switch", "manufacturer_name": "Cisco", "model_name": "CBS350-24FP",
            "status": "active", "location": location.pk, "rack": "", "height_u": 1, "face": "front",
        })
        assert Device.objects.get(name="SW-CCTV-01").ports.count() == 26

    def test_edit_port_vlans_is_audited(self, as_user, engineer, sw):
        v10, v20 = VLAN.objects.create(vid=10, name="Mgmt"), VLAN.objects.create(vid=20, name="Servers")
        p = port(sw, "Gi1/0/1")
        as_user(engineer).post(reverse("dcim:port_edit", args=[p.pk]), {
            "name": p.name, "type": "copper", "mode": "trunk", "untagged_vlan": v10.pk, "tagged_vlans": [v20.pk],
            "enabled": "on",
        })
        p.refresh_from_db()
        assert p.mode == "trunk" and p.untagged_vlan == v10 and list(p.tagged_vlans.all()) == [v20]
        changes = [e.changes for e in AuditLog.objects.filter(object_repr=str(p), action="update")]
        assert {"tagged_vlans": [None, "20"]} in changes
        assert any(c.get("untagged_vlan") == [None, "10 · Mgmt"] for c in changes)

    def test_deleting_a_port_removes_its_cable(self, as_user, engineer, fw, sw):
        Connection.objects.create(a_port=port(fw, "port1"), b_port=port(sw, "Gi1/0/10"), function="Uplink")
        as_user(engineer).post(reverse("dcim:port_delete", args=[port(fw, "port1").pk]))
        assert not Connection.objects.exists() and not fw.ports.filter(name="port1").exists()


# --------------------------------------------------------------------------
# VLANs
# --------------------------------------------------------------------------


class TestVLANs:
    def test_global_and_per_location_uniqueness(self):
        VLAN.objects.create(vid=10, name="Mgmt")
        with pytest.raises(ValidationError, match="global VLAN"):
            VLAN(vid=10, name="Again").full_clean()
        hq = make_location("HQ")
        VLAN.objects.create(vid=10, name="HQ mgmt", location=hq)  # allowed alongside the global one
        with pytest.raises(ValidationError, match="already has a VLAN"):
            VLAN(vid=10, name="dup", location=hq).full_clean()

    def test_crud_and_delete_detaches_ports(self, as_user, engineer, sw):
        client = as_user(engineer)
        client.post(reverse("dcim:vlan_new"), {"vid": 30, "name": "CCTV", "description": ""})
        vlan = VLAN.objects.get(vid=30)
        p = port(sw, "Gi1/0/1")
        set_port_vlans(p, vlan, [vlan])
        client.post(reverse("dcim:vlan_delete", args=[vlan.pk]))
        p.refresh_from_db()
        assert p.untagged_vlan is None and not p.tagged_vlans.exists()

    @pytest.mark.parametrize("vid", [0, 4095])
    def test_vid_range(self, vid):
        with pytest.raises(ValidationError):
            VLAN(vid=vid, name="x").full_clean()


# --------------------------------------------------------------------------
# Connections
# --------------------------------------------------------------------------


def connect_post(a_port, b_port=None, external=None, **extra):
    data = {"a_device": a_port.device_id, "a_port": a_port.pk, "function": "Internet + VLAN trunk",
            "cable_label": "C-010", "cable_type": "cat6a", "colour": "", "length_m": "", "remark": ""}
    if b_port is not None:
        data.update(b_kind="device", b_device=b_port.device_id, b_port=b_port.pk)
    else:
        data.update(b_kind="external", b_external_name=external)
    data.update(extra)
    return data


class TestConnections:
    def test_entered_once_shown_on_both_devices(self, as_user, engineer, fw, sw):
        """PLAN.md Phase 3 'done when': firewall port1 → switch Gi1/0/10, Internet + VLAN trunk."""
        client = as_user(engineer)
        response = client.post(reverse("dcim:connection_new"),
                               connect_post(port(fw, "port1"), port(sw, "Gi1/0/10")))
        assert response.url == reverse("dcim:connections")
        assert Connection.objects.count() == 1
        for device, peer in ((fw, "Gi1/0/10"), (sw, "port1")):
            page = client.get(device.get_absolute_url())
            [connected] = [p for p in page.context["ports"] if p.conn]
            assert connected.peer.name == peer and "Internet + VLAN trunk" in page.content.decode()
        listing = client.get(reverse("dcim:connections")).content.decode()
        assert "FW-CORE-01" in listing and "SW-CORE-01" in listing and "Internet + VLAN trunk" in listing

    def test_a_port_can_only_have_one_cable(self, as_user, engineer, fw, sw):
        Connection.objects.create(a_port=port(fw, "port1"), b_port=port(sw, "Gi1/0/10"), function="x")
        # sw Gi1/0/10 is already side B; using it as side A of another cable must fail
        response = as_user(engineer).post(reverse("dcim:connection_new"),
                                          connect_post(port(sw, "Gi1/0/10"), port(fw, "port2")))
        assert response.status_code == 400 and "already connected" in response.content.decode()
        assert Connection.objects.count() == 1

    def test_database_refuses_port_on_both_sides(self, fw, sw):
        Connection.objects.create(a_port=port(fw, "port1"), b_port=port(sw, "Gi1/0/10"), function="x")
        with pytest.raises((IntegrityError, DatabaseError), match="already connected"), transaction.atomic():
            Connection.objects.create(a_port=port(sw, "Gi1/0/10"), b_port=port(fw, "port2"), function="y")

    def test_database_requires_exactly_one_b_side(self, fw, sw):
        with pytest.raises(IntegrityError), transaction.atomic():
            Connection.objects.create(a_port=port(fw, "port1"), function="nothing on side B")

    def test_cannot_connect_port_to_itself(self, as_user, engineer, fw):
        response = as_user(engineer).post(reverse("dcim:connection_new"),
                                          connect_post(port(fw, "port1"), port(fw, "port1")))
        assert response.status_code == 400 and "itself" in response.content.decode()

    def test_port_must_belong_to_chosen_device(self, as_user, engineer, fw, sw):
        data = connect_post(port(fw, "port1"), port(sw, "Gi1/0/1"))
        data["b_device"] = fw.pk
        response = as_user(engineer).post(reverse("dcim:connection_new"), data)
        assert response.status_code == 400 and "selected device" in response.content.decode()

    def test_external_endpoint_created_then_reused(self, as_user, engineer, fw):
        client = as_user(engineer)
        client.post(reverse("dcim:connection_new"), connect_post(port(fw, "wan1"), external="FibreLink ONT"))
        client.post(reverse("dcim:connection_new"), connect_post(port(fw, "wan2"), external="fibrelink ont"))
        assert ExternalEndpoint.objects.count() == 1 and Connection.objects.count() == 2

    def test_apply_mode_sets_both_ends(self, as_user, engineer, fw, sw):
        v10, v20 = VLAN.objects.create(vid=10, name="Mgmt"), VLAN.objects.create(vid=20, name="Servers")
        as_user(engineer).post(reverse("dcim:connection_new"), connect_post(
            port(fw, "port1"), port(sw, "Gi1/0/10"), apply_mode="trunk", untagged_vlan="",
            tagged_vlans=[v10.pk, v20.pk]))
        for p in (port(fw, "port1"), port(sw, "Gi1/0/10")):
            assert p.mode == "trunk" and sorted(p.tagged_vlans.values_list("vid", flat=True)) == [10, 20]

    def test_edit_moves_cable_and_delete_frees_ports(self, as_user, engineer, fw, sw):
        c = Connection.objects.create(a_port=port(fw, "port1"), b_port=port(sw, "Gi1/0/10"), function="x")
        client = as_user(engineer)
        client.post(reverse("dcim:connection_edit", args=[c.pk]), connect_post(port(fw, "port1"), port(sw, "Gi1/0/11")))
        c.refresh_from_db()
        assert c.b_port.name == "Gi1/0/11"
        update = AuditLog.objects.filter(action="update", object_id=str(c.pk)).first()
        assert update.changes["b_port"] == ["SW-CORE-01 · Gi1/0/10", "SW-CORE-01 · Gi1/0/11"]
        response = client.post(reverse("dcim:connection_delete", args=[c.pk]), {"return_device": fw.pk})
        assert response.url == f"{fw.get_absolute_url()}?tab=ports"
        assert not Connection.objects.exists()

    def test_list_filters(self, as_user, viewer, fw, sw):
        trunk = Connection.objects.create(a_port=port(fw, "port1"), b_port=port(sw, "Gi1/0/10"), function="Core trunk")
        set_port_vlans(trunk.a_port, None, [VLAN.objects.create(vid=30, name="CCTV")])
        Connection.objects.create(a_port=port(fw, "wan1"), b_external=ExternalEndpoint.objects.create(name="ISP"),
                                  function="Internet")
        client = as_user(viewer)
        functions = lambda r: {c.function for c in r.context["connections"]}  # noqa: E731
        assert functions(client.get(reverse("dcim:connections"), {"q": "trunk"})) == {"Core trunk"}
        assert functions(client.get(reverse("dcim:connections"), {"q": "30"})) == {"Core trunk"}  # VLAN ID
        assert functions(client.get(reverse("dcim:connections"), {"q": "isp"})) == {"Internet"}
        assert len(functions(client.get(reverse("dcim:connections"), {"device": sw.pk}))) == 1

    def test_port_picker_disables_connected_ports(self, as_user, engineer, fw, sw):
        Connection.objects.create(a_port=port(fw, "port1"), b_port=port(sw, "Gi1/0/10"), function="x")
        html = as_user(engineer).get(reverse("dcim:port_options"), {"b_device": sw.pk}).content.decode()
        assert "Gi1/0/10 (connected)" in html and "disabled" in html
        keep = as_user(engineer).get(reverse("dcim:port_options"),
                                     {"device": sw.pk, "keep": port(sw, "Gi1/0/10").pk}).content.decode()
        assert "Gi1/0/10 (connected)" not in keep  # editing that cable: its own port stays selectable

    def test_connection_audit_uses_names(self, as_user, engineer, fw, sw):
        as_user(engineer).post(reverse("dcim:connection_new"), connect_post(port(fw, "port1"), port(sw, "Gi1/0/10")))
        created = AuditLog.objects.get(action="create", object_type__model="connection")
        assert created.changes["a_port"] == [None, "FW-CORE-01 · port1"] and created.actor == engineer


# --------------------------------------------------------------------------
# Tracing
# --------------------------------------------------------------------------


class TestTrace:
    def names(self, hops):
        return [str(h["port"]) if h["port"] else f"EXT:{h['external']}" for h in hops]

    def test_through_patch_panel_from_either_cable(self, fw, sw, pp):
        c1 = Connection.objects.create(a_port=port(fw, "port2"), b_port=port(pp, "2"), function="trunk via patch")
        c2 = Connection.objects.create(a_port=port(pp, "2 (rear)"), b_port=port(sw, "Gi1/0/10"), function="patch")
        expected = ["FW-CORE-01 · port2", "PP-R1-01 · 2", "PP-R1-01 · 2 (rear)", "SW-CORE-01 · Gi1/0/10"]
        for c in (c1, c2):
            hops, cables = trace(c)
            assert self.names(hops) == expected and [x.pk for x in cables] == [c1.pk, c2.pk]
            assert hops[2]["passthrough"]

    def test_ends_at_external(self, sw, pp):
        Connection.objects.create(a_port=port(sw, "Gi1/0/1"), b_port=port(pp, "3"), function="AP")
        c2 = Connection.objects.create(a_port=port(pp, "3 (rear)"),
                                       b_external=ExternalEndpoint.objects.create(name="Wall jack B-12"), function="h")
        assert self.names(trace(c2)[0]) == ["SW-CORE-01 · Gi1/0/1", "PP-R1-01 · 3", "PP-R1-01 · 3 (rear)",
                                            "EXT:Wall jack B-12"]

    def test_direct_cable(self, fw, sw):
        c = Connection.objects.create(a_port=port(fw, "port1"), b_port=port(sw, "Gi1/0/1"), function="x")
        assert self.names(trace(c)[0]) == ["FW-CORE-01 · port1", "SW-CORE-01 · Gi1/0/1"]

    def test_trace_view(self, as_user, viewer, fw, sw):
        c = Connection.objects.create(a_port=port(fw, "port1"), b_port=port(sw, "Gi1/0/1"), function="Uplink")
        html = as_user(viewer).get(reverse("dcim:connection_trace", args=[c.pk])).content.decode()
        assert "FW-CORE-01" in html and "Gi1/0/1" in html and "Uplink" in html


def test_pattern_preview(as_user, engineer):
    html = as_user(engineer).get(reverse("dcim:pattern_preview"), {"patterns": "Gi1/0/[1-48]"}).content.decode()
    assert "48 ports" in html and "Gi1/0/1" in html and "Gi1/0/48" in html
    bad = as_user(engineer).get(reverse("dcim:pattern_preview"), {"patterns": "Gi[9-1]"})
    assert "backwards" in bad.content.decode()


def test_device_model_template_validation(as_user, engineer):
    model = make_model()
    response = as_user(engineer).post(reverse("inventory:device_model_edit", args=[model.pk]), {
        "manufacturer": model.manufacturer.pk, "name": model.name, "device_type": "switch", "height_u": 1,
        "port_template": "Gi[1-"})
    assert response.status_code == 400 and "unclosed" in response.content.decode()


def test_demo_loader_includes_ports_and_cables():
    call_command("load_demo_inventory")
    assert Connection.objects.count() == 28 and VLAN.objects.count() == 7
    fw = Device.objects.get(name="FW-CORE-01")
    assert port(fw, "port1").connection().b_port.name == "Gi1/0/10"


@pytest.mark.parametrize("name,short", [("Gi1/0/10", "10"), ("wan1", "wan1"), ("port11", "p11"), ("ether12", "e12"),
                                        ("sfp-sfpplus1", "s1"), ("mgmt", "mgmt"), ("iDRAC", "iDRA"), ("2 (rear)", "2")])
def test_front_panel_short_names(name, short):
    assert Port(name=name).short_name == short
