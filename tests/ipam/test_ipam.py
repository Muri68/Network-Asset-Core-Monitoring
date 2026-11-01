"""IP address management (PLAN.md Phase 4, §9 "IPAM")."""

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.ipam.models import ISP, Circuit, IPAddress, Prefix, prefix_for
from apps.ipam.views import next_free, utilisation
from tests.inventory.helpers import make_device

pytestmark = pytest.mark.django_db


def make_prefix(cidr, **kw):
    p = Prefix(cidr=cidr, **kw)
    p.full_clean()
    p.save()
    return p


# --------------------------------------------------------------------------
# Subnet maths
# --------------------------------------------------------------------------


class TestSubnetMaths:
    def test_done_when_from_plan(self):
        """PLAN.md Phase 4: 129.205.100.128/28 → .128 / .129–.142 / .143 / 255.255.255.240."""
        p = make_prefix("129.205.100.128/28")
        assert (p.network_address, p.netmask, p.broadcast) == ("129.205.100.128", "255.255.255.240", "129.205.100.143")
        assert (p.first_usable, p.last_usable, p.usable, p.total) == ("129.205.100.129", "129.205.100.142", 14, 16)
        assert p.gateway == "129.205.100.129" and p.wildcard == "0.0.0.15"

    def test_host_bits_are_refused_with_the_fix(self):
        with pytest.raises(ValidationError, match="the network is 129.205.100.128/28"):
            make_prefix("129.205.100.140/28")

    @pytest.mark.parametrize("cidr,usable,broadcast,first,last", [
        ("10.0.0.0/8", 16777214, "10.255.255.255", "10.0.0.1", "10.255.255.254"),
        ("10.10.0.0/24", 254, "10.10.0.255", "10.10.0.1", "10.10.0.254"),
        ("192.168.1.0/30", 2, "192.168.1.3", "192.168.1.1", "192.168.1.2"),
        ("192.168.1.8/31", 2, None, "192.168.1.8", "192.168.1.9"),   # RFC 3021: both usable, no broadcast
        ("192.168.1.7/32", 1, None, "192.168.1.7", "192.168.1.7"),
    ])
    def test_sizes_including_31_and_32(self, cidr, usable, broadcast, first, last):
        p = make_prefix(cidr)
        assert (p.usable, p.broadcast, p.first_usable, p.last_usable) == (usable, broadcast, first, last)

    def test_31_and_32_have_no_default_gateway(self):
        assert make_prefix("192.168.1.8/31").gateway is None

    @pytest.mark.parametrize("bad,message", [("10.0.0.0", "prefix length"), ("10.0.0.0/33", "isn't a valid"),
                                             ("2001:db8::/64", "Only IPv4"), ("banana/24", "isn't a valid")])
    def test_bad_input(self, bad, message):
        with pytest.raises(ValidationError, match=message):
            make_prefix(bad)

    def test_duplicate_prefix(self):
        make_prefix("10.10.0.0/24")
        with pytest.raises(ValidationError, match="already exists"):
            make_prefix("10.10.0.0/24")

    def test_gateway_must_be_usable(self):
        with pytest.raises(ValidationError, match="usable address"):
            make_prefix("10.10.0.0/24", gateway="10.10.0.255")
        with pytest.raises(ValidationError, match="usable address"):
            make_prefix("10.10.0.0/24", gateway="10.10.1.1")

    def test_kind_detected_automatically(self, as_user, engineer):
        client = as_user(engineer)
        form = client.get(reverse("ipam:prefix_new")).context["form"]
        assert form["kind"].value() in (None, "")  # the dropdown starts on "Automatic"
        client.post(reverse("ipam:prefix_new"), {"cidr": "129.205.100.128/28", "kind": "", "status": "active"})
        client.post(reverse("ipam:prefix_new"), {"cidr": "10.20.0.0/22", "kind": "", "status": "active"})
        assert Prefix.objects.get(cidr="129.205.100.128/28").kind == "public"
        assert Prefix.objects.get(cidr="10.20.0.0/22").kind == "private"


# --------------------------------------------------------------------------
# Containment
# --------------------------------------------------------------------------


class TestContainment:
    def test_most_specific_prefix_wins(self):
        big = make_prefix("10.0.0.0/8")
        small = make_prefix("10.10.0.0/24")
        assert prefix_for("10.10.0.5") == small and prefix_for("10.99.0.5") == big and prefix_for("8.8.8.8") is None
        assert small.parent() == big and list(big.children()) == [small]

    def test_ips_rehome_when_prefixes_are_added_and_removed(self):
        big = make_prefix("10.0.0.0/8")
        ip = IPAddress.objects.create(address="10.10.0.5")
        assert ip.prefix == big
        small = make_prefix("10.10.0.0/24")
        ip.refresh_from_db()
        assert ip.prefix == small
        small.delete()
        ip.refresh_from_db()
        assert ip.prefix == big


# --------------------------------------------------------------------------
# IP addresses
# --------------------------------------------------------------------------


class TestIPAddresses:
    def test_duplicate_ip_rejected(self):
        IPAddress.objects.create(address="129.205.100.131")
        with pytest.raises(ValidationError, match="already recorded"):
            IPAddress(address="129.205.100.131").full_clean()

    def test_network_and_broadcast_cannot_be_assigned(self):
        make_prefix("129.205.100.128/28")
        for address, role in (("129.205.100.128", "network"), ("129.205.100.143", "broadcast")):
            with pytest.raises(ValidationError, match=f"the {role} address"):
                IPAddress(address=address).full_clean()

    def test_clash_with_another_devices_management_ip(self):
        fw = make_device("FW", rack=None, mgmt_ip="10.10.0.1")
        sw = make_device("SW", rack=None)
        with pytest.raises(ValidationError, match="management IP of FW"):
            IPAddress(address="10.10.0.1", device=sw).full_clean()
        IPAddress(address="10.10.0.1", device=fw).full_clean()  # same device: fine

    def test_device_management_ip_cannot_take_an_ipam_address(self):
        other = make_device("WEB", rack=None)
        IPAddress.objects.create(address="10.10.10.11", device=other, service="Portal")
        with pytest.raises(ValidationError, match="already assigned to WEB"):
            make_device("NEW", rack=None, mgmt_ip="10.10.10.11")
        IPAddress.objects.create(address="10.10.10.50", status="reserved", service="Future DB")
        with pytest.raises(ValidationError, match="recorded in IPAM"):
            make_device("NEW2", rack=None, mgmt_ip="10.10.10.50")

    def test_port_must_belong_to_device(self):
        from apps.dcim.services import create_ports

        fw, sw = make_device("FW", rack=None), make_device("SW", rack=None)
        (port,), _ = create_ports(sw, ["Gi1"])
        with pytest.raises(ValidationError, match="port on the selected device"):
            IPAddress(address="10.0.0.9", device=fw, port=port).full_clean()

    def test_assign_via_form_with_nat_and_audit(self, as_user, engineer):
        make_prefix("129.205.100.128/28")
        web = make_device("SRV-WEB-01", rack=None, mgmt_ip="10.10.10.11")
        client = as_user(engineer)
        response = client.post(reverse("ipam:ip_new"), {
            "address": "129.205.100.131", "status": "used", "device": web.pk, "service": "Customer portal",
            "dns_name": "portal.acme.example", "nat_to": "10.10.10.11", "nat_ports": "443, 80", "description": "",
        })
        ip = IPAddress.objects.get(address="129.205.100.131")
        assert response.url == ip.prefix.get_absolute_url()
        assert ip.device == web and ip.nat_to == "10.10.10.11" and ip.created_by == engineer
        created = AuditLog.objects.get(action="create", object_repr="129.205.100.131")
        assert created.changes["device"] == [None, "SRV-WEB-01"] and created.changes["nat_to"] == [None, "10.10.10.11"]
        page = client.get(ip.prefix.get_absolute_url()).content.decode()
        assert "Customer portal" in page and "→ 10.10.10.11" in page
        device_page = client.get(web.get_absolute_url()).content.decode()
        assert "129.205.100.131" in device_page and "10.10.10.11" in device_page

    def test_free_an_address(self, as_user, engineer):
        p = make_prefix("10.10.0.0/24")
        ip = IPAddress.objects.create(address="10.10.0.20", service="Printer")
        response = as_user(engineer).post(reverse("ipam:ip_delete", args=[ip.pk]))
        assert response.url == p.get_absolute_url() and not IPAddress.objects.exists()
        assert AuditLog.objects.filter(action="delete", object_repr="10.10.0.20").exists()

    def test_viewer_cannot_assign(self, as_user, viewer):
        response = as_user(viewer).post(reverse("ipam:ip_new"), {"address": "10.0.0.5", "status": "used"})
        assert response.status_code == 403


# --------------------------------------------------------------------------
# Utilisation & the prefix page
# --------------------------------------------------------------------------


class TestUtilisation:
    def test_counts_records_management_ips_and_gateway(self):
        p = make_prefix("10.10.0.0/24")  # gateway .1
        make_device("SW", rack=None, mgmt_ip="10.10.0.2")
        IPAddress.objects.create(address="10.10.0.10", status="reserved")
        IPAddress.objects.create(address="10.10.1.10")  # outside: not counted
        assert utilisation(p) == (3, 1)
        assert next_free(p) == "10.10.0.3"

    def test_full_prefix_has_no_next_free(self):
        p = make_prefix("192.168.1.8/31")
        IPAddress.objects.create(address="192.168.1.8")
        IPAddress.objects.create(address="192.168.1.9")
        assert utilisation(p) == (2, 100) and next_free(p) is None

    def test_prefix_page_rows(self, as_user, viewer):
        p = make_prefix("129.205.100.128/28")
        IPAddress.objects.create(address="129.205.100.130", service="Firewall WAN")
        page = as_user(viewer).get(p.get_absolute_url())
        rows = {r["address"]: r for r in page.context["rows"]}
        assert len(rows) == 16
        assert rows["129.205.100.128"]["role"] == "network" and rows["129.205.100.143"]["role"] == "broadcast"
        assert rows["129.205.100.129"]["role"] == "gateway" and rows["129.205.100.130"]["status"] == "used"
        assert rows["129.205.100.131"]["status"] == "free" and page.context["next_free"] == "129.205.100.131"

    def test_large_prefix_is_paginated_and_defaults_to_used(self, as_user, viewer):
        p = make_prefix("10.0.0.0/16")
        IPAddress.objects.create(address="10.0.200.1")
        client = as_user(viewer)
        used = client.get(p.get_absolute_url())
        assert used.context["show"] == "used" and [r["address"] for r in used.context["rows"]] == ["10.0.0.1",
                                                                                                   "10.0.200.1"]
        all_page = client.get(p.get_absolute_url(), {"show": "all", "page": 2})
        assert len(all_page.context["rows"]) == 256 and all_page.context["rows"][0]["address"] == "10.0.1.0"
        assert len(all_page.context["map"]) == 256  # address map capped

    def test_prefix_list_search_by_ip(self, as_user, viewer):
        make_prefix("10.10.0.0/24", description="Management")
        make_prefix("10.20.0.0/22", description="CCTV")
        page = as_user(viewer).get(reverse("ipam:prefixes"), {"q": "10.20.1.21"})
        assert [p.cidr for p in page.context["prefixes"]] == ["10.20.0.0/22"]

    def test_dashboard_shows_busiest_prefixes(self, as_user, viewer):
        p = make_prefix("192.168.1.0/30", description="Tiny link")
        IPAddress.objects.create(address="192.168.1.2")
        html = as_user(viewer).get(reverse("ui:dashboard")).content.decode()
        assert "192.168.1.0/30" in html and "Tiny link" in html and p.get_absolute_url() in html


# --------------------------------------------------------------------------
# ISPs & circuits
# --------------------------------------------------------------------------


class TestISPs:
    def test_create_isp_and_circuit(self, as_user, engineer):
        client = as_user(engineer)
        client.post(reverse("ipam:isp_new"), {"name": "FibreLink Networks", "support_phone": "+234 800 000 1100",
                                              "support_email": "noc@fibrelink.example"})
        isp = ISP.objects.get()
        client.post(reverse("ipam:circuit_new"), {"isp": isp.pk, "circuit_id": "FL-ABJ-22817", "circuit_type": "fibre",
                                                  "status": "active", "bandwidth": "200 / 200 Mbps",
                                                  "monthly_cost": "1450000", "currency": "ngn",
                                                  "contract_start": "2026-01-01", "contract_end": "2027-06-01"})
        circuit = Circuit.objects.get()
        assert circuit.currency == "NGN" and circuit.isp == isp
        page = client.get(reverse("ipam:isps")).content.decode()
        assert "FL-ABJ-22817" in page and "noc@fibrelink.example" in page

    def test_contract_dates_checked(self):
        isp = ISP.objects.create(name="X")
        import datetime

        c = Circuit(isp=isp, circuit_id="C1", contract_start=datetime.date(2027, 1, 1),
                    contract_end=datetime.date(2026, 1, 1))
        with pytest.raises(ValidationError, match="can't end before"):
            c.full_clean()

    def test_isp_with_circuits_cannot_be_deleted(self, as_user, engineer):
        isp = ISP.objects.create(name="X")
        Circuit.objects.create(isp=isp, circuit_id="C1")
        as_user(engineer).post(reverse("ipam:isp_delete", args=[isp.pk]))
        assert ISP.objects.exists()


def test_demo_loader_includes_ipam():
    call_command("load_demo_inventory")
    p = Prefix.objects.get(cidr="129.205.100.128/28")
    assert p.isp.name == "FibreLink Networks" and utilisation(p) == (7, 50)
    assert IPAddress.objects.get(address="129.205.100.131").nat_to == "10.10.10.11"
