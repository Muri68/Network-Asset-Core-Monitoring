"""Inventory & racks (PLAN.md Phase 2)."""

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.inventory.models import Device, DeviceAttribute, DeviceModel, Location, Manufacturer, Rack, normalise_mac

from .helpers import make_device, make_location, make_model, make_rack

pytestmark = pytest.mark.django_db


def device_post(**overrides):
    rack = overrides.pop("rack_obj", None) or make_rack()
    data = {
        "name": "FW-CORE-01", "device_type": "firewall", "manufacturer_name": "Fortinet",
        "model_name": "FortiGate 200F", "status": "active", "location": rack.location.pk, "rack": rack.pk,
        "position_u": 41, "height_u": 1, "face": "front", "mgmt_ip": "10.10.0.1", "mac": "AA-BB-CC-00-11-22",
        "serial": "FGT200F123", "asset_tag": "", "firmware": "FortiOS 7.4.5", "owner": "Network team",
        "notes": "", "parent": "", "purchase_date": "", "warranty_expiry": "2027-03-14",
        "attr_key": ["HA role", "Console"], "attr_value": ["Standalone", "RJ45 9600 8N1"],
    }
    data.update(overrides)
    return data


# --------------------------------------------------------------------------
# Rack placement rules
# --------------------------------------------------------------------------


class TestRackPlacement:
    def test_device_must_fit_in_rack(self):
        rack = make_rack(height_u=42)
        with pytest.raises(ValidationError, match="doesn't fit"):
            make_device(rack=rack, position_u=42, height_u=2)
        make_device(rack=rack, position_u=41, height_u=2)  # U41–U42 fits

    def test_overlap_on_same_face_is_refused(self):
        rack = make_rack()
        make_device("SW-CORE-01", rack=rack, position_u=10, height_u=2)  # U10–U11
        with pytest.raises(ValidationError, match="Overlaps SW-CORE-01"):
            make_device(rack=rack, position_u=11)
        make_device(rack=rack, position_u=12)  # just above: fine

    def test_rear_face_is_separate(self):
        rack = make_rack()
        make_device(rack=rack, position_u=5, face="front")
        make_device(rack=rack, position_u=5, face="rear")

    def test_editing_a_device_does_not_conflict_with_itself(self):
        device = make_device(position_u=20, height_u=2)
        device.notes = "changed"
        device.full_clean()

    def test_position_without_rack_is_refused(self):
        device = make_device(rack=None, save=False)
        device.position_u = 3
        with pytest.raises(ValidationError, match="Choose a rack"):
            device.full_clean()

    def test_rack_in_other_location_is_refused_by_model(self):
        rack = make_rack(location=make_location("Branch"))
        device = make_device(rack=rack, save=False, location=make_location("HQ"))
        with pytest.raises(ValidationError, match="is in Branch"):
            device.full_clean()

    def test_dependency_loops_are_refused(self):
        a = make_device("A", rack=None)
        b = make_device("B", rack=None, parent=a)
        a.parent = b
        with pytest.raises(ValidationError, match="loop"):
            a.full_clean()
        a.parent = a
        with pytest.raises(ValidationError, match="itself"):
            a.full_clean()


class TestDeviceFields:
    def test_names_are_unique_ignoring_case(self):
        make_device("SW-CORE-01", rack=None)
        with pytest.raises(ValidationError, match="already exists"):
            make_device("sw-core-01", rack=None)

    def test_management_ip_and_asset_tag_unique(self):
        make_device(rack=None, mgmt_ip="10.0.0.1", asset_tag="ACME-1")
        with pytest.raises(ValidationError, match="management IP"):
            make_device(rack=None, mgmt_ip="10.0.0.1")
        with pytest.raises(ValidationError, match="asset tag"):
            make_device(rack=None, asset_tag="ACME-1")
        make_device(rack=None, asset_tag="")  # several blanks are fine
        make_device(rack=None, asset_tag="")

    @pytest.mark.parametrize("raw", ["AA-BB-CC-DD-EE-FF", "aabb.ccdd.eeff", "aabbccddeeff", "aa:bb:cc:dd:ee:ff"])
    def test_mac_is_normalised(self, raw):
        assert normalise_mac(raw) == "aa:bb:cc:dd:ee:ff"

    def test_bad_mac_refused(self):
        with pytest.raises(ValidationError):
            make_device(rack=None, mac="not-a-mac")


# --------------------------------------------------------------------------
# Device pages
# --------------------------------------------------------------------------


class TestDeviceViews:
    def test_create_device_with_new_manufacturer_model_and_attributes(self, as_user, engineer):
        response = as_user(engineer).post(reverse("inventory:device_new"), device_post())
        device = Device.objects.get(name="FW-CORE-01")
        assert response.url == device.get_absolute_url()
        assert device.device_model.manufacturer.name == "Fortinet" and device.device_model.device_type == "firewall"
        assert device.mac == "aa:bb:cc:00:11:22" and device.created_by == engineer
        assert dict(device.attributes.values_list("key", "value")) == {"HA role": "Standalone",
                                                                       "Console": "RJ45 9600 8N1"}

    def test_existing_manufacturer_reused_case_insensitively(self, as_user, engineer):
        make_model("Fortinet", "FortiGate 200F", "firewall")
        as_user(engineer).post(reverse("inventory:device_new"),
                               device_post(manufacturer_name="FORTINET", model_name="fortigate 200f"))
        assert Manufacturer.objects.count() == 1 and DeviceModel.objects.count() == 1

    def test_rack_sets_location(self, as_user, engineer):
        rack = make_rack(location=make_location("Branch"))
        other = make_location("HQ")
        as_user(engineer).post(reverse("inventory:device_new"), device_post(rack_obj=rack, location=other.pk))
        assert Device.objects.get().location == rack.location

    def test_overlap_shows_error_on_form(self, as_user, engineer):
        rack = make_rack()
        make_device("SW-CORE-01", rack=rack, position_u=41)
        response = as_user(engineer).post(reverse("inventory:device_new"), device_post(rack_obj=rack))
        assert response.status_code == 400
        assert "Overlaps SW-CORE-01" in response.content.decode()
        assert not Device.objects.filter(name="FW-CORE-01").exists()

    def test_unracked_device_ignores_position(self, as_user, engineer):
        location = make_location()
        as_user(engineer).post(reverse("inventory:device_new"), device_post(rack="", location=location.pk))
        device = Device.objects.get(name="FW-CORE-01")
        assert device.rack is None and device.position_u is None

    def test_duplicate_attribute_names_rejected(self, as_user, engineer):
        response = as_user(engineer).post(reverse("inventory:device_new"),
                                          device_post(attr_key=["Role", "role"], attr_value=["a", "b"]))
        assert response.status_code == 400 and "listed twice" in response.content.decode()

    def test_edit_updates_and_removes_attributes(self, as_user, engineer):
        client = as_user(engineer)
        client.post(reverse("inventory:device_new"), device_post())
        device = Device.objects.get()
        client.post(reverse("inventory:device_edit", args=[device.pk]),
                    device_post(rack_obj=device.rack, attr_key=["HA role"], attr_value=["Primary"]))
        assert dict(device.attributes.values_list("key", "value")) == {"HA role": "Primary"}

    def test_new_device_form_prefilled_from_rack_slot(self, as_user, engineer):
        rack = make_rack()
        page = as_user(engineer).get(reverse("inventory:device_new"), {"rack": rack.pk, "position_u": 7})
        assert page.context["form"].initial["position_u"] == "7"
        assert page.context["form"].initial["location"] == rack.location_id

    def test_viewer_cannot_create(self, as_user, viewer):
        assert as_user(viewer).post(reverse("inventory:device_new"), device_post()).status_code == 403
        assert not Device.objects.exists()

    def test_retire_frees_rack_space_and_keeps_record(self, as_user, engineer):
        device = make_device(position_u=10)
        as_user(engineer).post(reverse("inventory:device_retire", args=[device.pk]))
        device.refresh_from_db()
        assert device.status == "retired" and device.rack is None and device.position_u is None
        make_device(rack=make_rack(), position_u=10)  # the slot is free again

    def test_only_admin_can_delete_and_must_type_name(self, as_user, admin, engineer):
        device = make_device("SW-OLD", rack=None)
        DeviceAttribute.objects.create(device=device, key="k", value="v")
        assert as_user(engineer).post(reverse("inventory:device_delete", args=[device.pk]),
                                      {"confirm_name": "SW-OLD"}).status_code == 403
        client = as_user(admin)
        client.post(reverse("inventory:device_delete", args=[device.pk]), {"confirm_name": "wrong"})
        assert Device.objects.filter(pk=device.pk).exists()
        client.post(reverse("inventory:device_delete", args=[device.pk]), {"confirm_name": "sw-old"})
        assert not Device.objects.filter(pk=device.pk).exists()
        assert AuditLog.objects.filter(action="delete", object_repr="SW-OLD").exists()

    def test_list_filters(self, as_user, viewer):
        make_device("SW-A", rack=None, device_type="switch", mgmt_ip="10.1.1.1")
        make_device("CAM-A", rack=None, device_type="camera")
        make_device("OLD-A", rack=None, status="retired")
        client = as_user(viewer)
        names = lambda r: {d.name for d in r.context["devices"]}  # noqa: E731
        assert names(client.get(reverse("inventory:devices"))) == {"SW-A", "CAM-A"}  # retired hidden
        assert names(client.get(reverse("inventory:devices"), {"type": "camera"})) == {"CAM-A"}
        assert names(client.get(reverse("inventory:devices"), {"q": "10.1.1"})) == {"SW-A"}
        assert names(client.get(reverse("inventory:devices"), {"status": "retired"})) == {"OLD-A"}
        assert len(names(client.get(reverse("inventory:devices"), {"status": "all"}))) == 3

    def test_detail_shows_history_with_readable_changes(self, as_user, engineer):
        rack_a, rack_b = make_rack("Rack A"), make_rack("Rack B")
        device = make_device(rack=rack_a)
        client = as_user(engineer)
        client.post(reverse("inventory:device_edit", args=[device.pk]),
                    device_post(name=device.name, rack_obj=rack_b, mgmt_ip="", position_u=5))
        update = AuditLog.objects.filter(action="update", object_id=str(device.pk)).first()
        assert update.changes["rack"] == ["Rack A", "Rack B"]
        html = client.get(device.get_absolute_url()).content.decode()
        assert "Rack A" in html and "Rack B" in html


# --------------------------------------------------------------------------
# Racks & locations
# --------------------------------------------------------------------------


class TestRacksAndLocations:
    def test_create_location_and_rack(self, as_user, engineer):
        client = as_user(engineer)
        client.post(reverse("inventory:location_new"), {"name": "HQ", "address": "", "notes": ""})
        location = Location.objects.get(name="HQ")
        response = client.post(reverse("inventory:rack_new"), {"location": location.pk, "name": "Rack 1",
                                                               "height_u": 42, "notes": ""})
        rack = Rack.objects.get(name="Rack 1")
        assert response.url == rack.get_absolute_url()

    def test_duplicate_rack_name_in_location(self, as_user, engineer):
        rack = make_rack("Rack 1")
        response = as_user(engineer).post(reverse("inventory:rack_new"),
                                          {"location": rack.location.pk, "name": "Rack 1", "height_u": 42})
        assert response.status_code == 400 and "already has a rack" in response.content.decode()

    def test_rack_cannot_shrink_below_devices(self, as_user, engineer):
        rack = make_rack(height_u=42)
        make_device(rack=rack, position_u=40)
        response = as_user(engineer).post(reverse("inventory:rack_edit", args=[rack.pk]),
                                          {"location": rack.location.pk, "name": rack.name, "height_u": 24})
        assert response.status_code == 400 and "U40" in response.content.decode()

    def test_rack_and_location_with_contents_cannot_be_deleted(self, as_user, engineer):
        rack = make_rack()
        make_device(rack=rack)
        client = as_user(engineer)
        client.post(reverse("inventory:rack_delete", args=[rack.pk]))
        client.post(reverse("inventory:location_delete", args=[rack.location.pk]))
        assert Rack.objects.filter(pk=rack.pk).exists() and Location.objects.filter(pk=rack.location.pk).exists()

    def test_empty_rack_and_location_can_be_deleted(self, as_user, engineer):
        rack = make_rack()
        location = rack.location
        client = as_user(engineer)
        client.post(reverse("inventory:rack_delete", args=[rack.pk]))
        client.post(reverse("inventory:location_delete", args=[location.pk]))
        assert not Rack.objects.exists() and not Location.objects.exists()

    def test_rack_page_layout(self, as_user, viewer):
        rack = make_rack(height_u=42)
        make_device("TOP", rack=rack, position_u=41, height_u=2)
        page = as_user(viewer).get(rack.get_absolute_url())
        front = page.context["front"]
        assert front["used"] == 2 and 41 not in front["free_units"] and 40 in front["free_units"]
        assert front["items"][0].top_px == 1  # top of the rack
        assert "Add at U" not in page.content.decode()  # viewers can't add from empty slots

    @pytest.mark.parametrize("params,expected", [
        ({"position_u": 10, "height_u": 1}, "Overlaps SW-1"),
        ({"position_u": 12, "height_u": 1}, "is free"),
        ({"position_u": 42, "height_u": 2}, "Doesn&#x27;t fit"),
    ])
    def test_live_rack_check(self, as_user, engineer, params, expected):
        rack = make_rack(height_u=42)
        make_device("SW-1", rack=rack, position_u=10, height_u=2)
        response = as_user(engineer).get(reverse("inventory:rack_check"), {"rack": rack.pk, "face": "front", **params})
        assert expected in response.content.decode()


def test_audit_records_inventory_changes_with_names(as_user, engineer):
    as_user(engineer).post(reverse("inventory:device_new"), device_post())
    created = AuditLog.objects.get(action="create", object_repr="FW-CORE-01")
    assert created.actor == engineer
    assert created.changes["location"] == [None, "HQ — Server Room"] and created.changes["rack"] == [None, "Rack 1"]
    assert created.changes["device_type"] == [None, "Firewall"]
    assert AuditLog.objects.filter(action="create", object_repr="FW-CORE-01 · HA role").exists()


def test_demo_inventory_loads_once():
    call_command("load_demo_inventory")
    assert Device.objects.count() == 30 and Rack.objects.count() == 3
    assert Device.objects.get(name="SW-CCTV-01").children.count() == 6  # 2 NVRs + 4 cameras
    from django.core.management import CommandError

    with pytest.raises(CommandError):
        call_command("load_demo_inventory")


def test_dashboard_counts_real_devices(as_user, viewer):
    make_device("A", rack=None)
    make_device("B", rack=None, status="retired")
    assert as_user(viewer).get(reverse("ui:dashboard")).context["devices_total"] == 1
