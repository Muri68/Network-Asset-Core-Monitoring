"""Rack connection map export (the owner's network-mapping spreadsheet layout)."""

import io

import pytest
from django.urls import reverse
from openpyxl import load_workbook

from apps.audit.models import AuditLog
from apps.dataio.rackmap import port_label
from apps.dcim.models import Connection, ExternalEndpoint, Port
from apps.dcim.services import create_ports
from apps.inventory.models import Location, Rack
from tests.inventory.helpers import make_device

pytestmark = pytest.mark.django_db


@pytest.fixture
def racks():
    """Rack 1 like the owner's sheet, plus Rack 2 holding the distribution switch."""
    hq = Location.objects.create(name="NACWC Data Centre")
    rack1 = Rack.objects.create(location=hq, name="Rack 1")
    rack2 = Rack.objects.create(location=hq, name="Rack 2")
    make_device("Fiber Patch Panel 42", rack=rack1, position_u=42, location=hq)
    fpp = make_device("Fiber Patch Panel 40", rack=rack1, position_u=40, location=hq,
                      purpose="Fibre connectivity to NACWC SLDRS QTR and NACWS")
    core = make_device("Fortinet 224E Core Switch", rack=rack1, position_u=35, location=hq,
                       purpose="Core/internal switching; carries internal VLANs", notes="Managed by the 201E")
    fw = make_device("FortiGate 201E Firewall", rack=rack1, position_u=33, location=hq, height_u=1)
    dist = make_device("Distribution Switch", rack=rack2, position_u=36, height_u=2, location=hq)
    (p7,), _ = create_ports(fpp, ["7"])
    core_ports, _ = create_ports(core, ["port4", "port8", "port20"])
    fw_ports, _ = create_ports(fw, ["port1", "WAN2"])
    (d1,), _ = create_ports(dist, ["port1"])
    Connection.objects.create(a_port=p7, b_external=ExternalEndpoint.objects.create(name="SLDRS QTR media converter"),
                              function="Fibre to SLDRS")
    Connection.objects.create(a_port=fw_ports[0], b_port=core_ports[0], function="Inside interface")
    trunk = core_ports[2]
    trunk.mode = Port.Mode.TRUNK
    trunk.save()
    Connection.objects.create(a_port=trunk, b_port=d1, function="Trunk to distribution")
    return rack1, rack2


def sheet(content, title="Rack 1"):
    return load_workbook(io.BytesIO(content))[title]


def test_port_labels():
    assert [port_label(Port(name=n)) for n in ("port4", "Port 9", "Gi1/0/4", "WAN2", "7", "eth0")] == [
        "Port 4", "Port 9", "Gi1/0/4", "WAN2", "Port 7", "eth0"]


def test_rack_sheet_layout(as_user, engineer, racks):
    rack1, _ = racks
    response = as_user(engineer).get(reverse("dataio:rack_map", args=[rack1.pk]))
    assert response["Content-Disposition"].startswith('attachment; filename="netcore-connection-map-rack-1-')
    ws = sheet(response.content)
    assert [c.value for c in ws[2]] == ["SERIAL", "RACK NUMBER", "RACK UNIT", "DEVICE", "CONNECTED TO", "PURPOSE",
                                        "REMARK"]
    rows = list(ws.iter_rows(min_row=3, values_only=True))
    assert [r[2] for r in rows] == ["42", "40", "35", "33"]  # top of the rack first
    assert [r[0] for r in rows] == [1, 2, 3, 4] and rows[0][1] == "1"  # rack number merged down the column
    assert "B3:B6" in {str(m) for m in ws.merged_cells.ranges}

    assert rows[0][4] == "Not in use" and "E3:F3" in {str(m) for m in ws.merged_cells.ranges}
    assert str(rows[1][4]) == "a. Port 7 to SLDRS QTR media converter (Fibre to SLDRS)"
    core = str(rows[2][4]).split("\n")
    assert core[0] == "a. Port 4 to FortiGate 201E Firewall Port 1 (Inside interface)"
    assert core[1] == "b. Port 20 TRUNK to Distribution Switch Port 1 on RACK 2 Unit 36 - 37 (Trunk to distribution)"
    assert rows[2][5].startswith("Core/internal switching") and rows[2][6] == "Managed by the 201E"
    assert str(rows[3][4]) == "a. Port 1 to Fortinet 224E Core Switch Port 4 (Inside interface)"


def test_bold_letters_and_trunk(as_user, engineer, racks):
    rack1, _ = racks
    content = as_user(engineer).get(reverse("dataio:rack_map", args=[rack1.pk])).content
    book = load_workbook(io.BytesIO(content), rich_text=True)
    cell = book["Rack 1"]["E5"].value
    bold = [str(block.text).strip() for block in cell if hasattr(block, "font") and block.font.b]
    assert bold == ["a.", "b.", "TRUNK"]


def test_print_setup(as_user, engineer, racks):
    ws = sheet(as_user(engineer).get(reverse("dataio:rack_map", args=[racks[0].pk])).content)
    assert ws.page_setup.orientation == "landscape" and ws.page_setup.fitToWidth == 1
    assert ws.print_title_rows == "$2:$2"


def test_all_racks_and_audit(as_user, engineer, racks):
    content = as_user(engineer).get(reverse("dataio:rack_map", args=["all"])).content
    assert load_workbook(io.BytesIO(content)).sheetnames == ["Rack 1", "Rack 2"]
    assert AuditLog.objects.filter(action="export", message__startswith="Connection map, all racks").exists()


def test_unknown_rack(as_user, engineer, racks):
    assert as_user(engineer).get(reverse("dataio:rack_map", args=["999"])).status_code == 404


def test_buttons(as_user, engineer, racks):
    client = as_user(engineer)
    url = reverse("dataio:rack_map", args=[racks[0].pk])
    assert url in client.get(racks[0].get_absolute_url()).content.decode()
    assert reverse("dataio:rack_map", args=["all"]) in client.get(reverse("dcim:connections")).content.decode()


def test_viewers_cannot_export(as_user, viewer, racks):
    assert as_user(viewer).get(reverse("dataio:rack_map", args=["all"])).status_code == 403
