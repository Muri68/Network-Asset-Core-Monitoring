"""Import / export (PLAN.md §7.6, §9 "Import")."""

import csv
import html as html_lib
import io
import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from openpyxl import load_workbook

from apps.audit.models import AuditLog
from apps.dataio import importer
from apps.dataio.files import read_upload, write_csv, write_xlsx
from apps.dcim.models import Connection, Port
from apps.inventory.models import Device, Location, Rack
from apps.ipam.models import IPAddress, Prefix
from apps.monitoring.models import Monitor

pytestmark = pytest.mark.django_db


def xlsx(*sheets):
    """xlsx(("Devices", headers, rows), …) → an uploaded workbook."""
    return SimpleUploadedFile("network.xlsx", write_xlsx(list(sheets)))


def csv_file(headers, rows, name="devices.csv", delimiter=","):
    out = io.StringIO()
    writer = csv.writer(out, delimiter=delimiter)
    writer.writerow(headers)
    writer.writerows(rows)
    return SimpleUploadedFile(name, out.getvalue().encode("utf-8-sig"))


def preview(client, upload, resource=""):
    return client.post(reverse("dataio:preview"), {"file": upload, "resource": resource})


def page(response):
    """The response as text, with HTML entities (&#x27; …) turned back into characters."""
    return html_lib.unescape(response.content.decode())


def token_of(response):
    match = re.search(r'name="token" value="([0-9a-f]+)"', page(response))
    return match.group(1) if match else None


def big_workbook():
    """205 rows: a location, 2 racks, 2 VLANs, 10 switches + 40 cameras, 100 ports, 50 connections."""
    switches = [f"SW-{i:02d}" for i in range(10)]
    devices = [[sw, "Switch", "Cisco", "CBS350-24", "", f"Rack {1 + i // 5}", str(30 + i % 5), "1",
                f"10.10.0.{10 + i}", ""] for i, sw in enumerate(switches)]
    devices += [[f"CAM-{i:02d}", "Camera", "Hikvision", "DS-2CD2143", "HQ", "", "", "", f"10.20.0.{10 + i}",
                 switches[i % 10]] for i in range(40)]
    ports = [[sw, f"Gi0/{p}", "Copper (RJ45)", "1 Gb", "Access", "10", "", "yes"] for sw in switches
             for p in range(1, 11)]
    connections = [[switches[2 * k], f"Gi0/{p}", switches[2 * k + 1], f"Gi0/{p}", f"Link {k}-{p}", "Cat6"]
                   for k in range(5) for p in range(1, 11)]
    return (
        ("Locations", ["Name", "Address"], [["HQ", "Marina"]]),
        ("Racks", ["Location", "Name", "Height (U)"], [["HQ", "Rack 1", "42"], ["HQ", "Rack 2", "42"]]),
        ("VLANs", ["VLAN ID", "Name", "Location"], [["10", "Management", ""], ["20", "CCTV", "HQ"]]),
        ("Devices", ["Name", "Type", "Manufacturer", "Model", "Location", "Rack", "Position (U)", "Height (U)",
                     "Management IP", "Parent"], devices),
        ("Ports", ["Device", "Port", "Type", "Speed", "Mode", "Untagged VLAN", "Tagged VLANs", "Enabled"], ports),
        ("Connections", ["Device A", "Port A", "Device B", "Port B", "Function", "Cable type"], connections),
    )


class TestBigImport:
    def test_preview_saves_nothing_then_import_saves_everything(self, as_user, admin):
        client = as_user(admin)
        response = preview(client, xlsx(*big_workbook()))
        html = page(response)
        assert response.status_code == 200 and "Error" not in html.split("<tbody>", 1)[1]
        assert Device.objects.count() == 0 and Location.objects.count() == 0  # dry run rolled back
        assert "Import 205 changes" in html

        client.post(reverse("dataio:confirm"), {"token": token_of(response)})
        assert (Location.objects.count(), Rack.objects.count(), Device.objects.count(), Port.objects.count(),
                Connection.objects.count()) == (1, 2, 50, 100, 50)
        cam = Device.objects.get(name="CAM-13")
        assert cam.parent.name == "SW-03" and cam.device_model.manufacturer.name == "Hikvision"
        assert Port.objects.get(device__name="SW-00", name="Gi0/1").untagged_vlan.vid == 10
        entry = AuditLog.objects.get(action="import")
        assert "Devices: 50 new" in entry.message and entry.actor == admin

    def test_importing_again_changes_nothing(self, as_user, admin):
        client = as_user(admin)
        client.post(reverse("dataio:confirm"), {"token": token_of(preview(client, xlsx(*big_workbook())))})
        html = page(preview(client, xlsx(*big_workbook())))
        assert "already up to date" in html and "205 unchanged" not in html  # counted per sheet
        assert html.count("No change") == 205

    def test_export_everything_round_trips(self, as_user, admin):
        client = as_user(admin)
        client.post(reverse("dataio:confirm"), {"token": token_of(preview(client, xlsx(*big_workbook())))})
        exported = client.get(reverse("dataio:export", args=["all", "xlsx"])).content
        html = page(preview(client, SimpleUploadedFile("export.xlsx", exported)))
        assert "already up to date" in html and "pill-down" not in html


class TestConflicts:
    def test_file_with_conflicts_is_rejected_with_row_errors(self, as_user, admin):
        from tests.inventory.helpers import make_device

        existing = make_device("EXISTING", mgmt_ip="10.10.0.99")
        hq = existing.location.name
        client = as_user(admin)
        upload = xlsx(
            ("Racks", ["Location", "Name"], [["Nowhere", "R9"]]),
            ("Devices", ["Name", "Type", "Manufacturer", "Model", "Location", "Rack", "Position (U)",
                         "Management IP"], [
                ["SW-A", "Switch", "Cisco", "X", hq, "", "", "10.10.0.99"],          # row 2: IP taken
                ["SW-B", "Toaster", "Cisco", "X", hq, "", "", ""],                   # row 3: bad type
                ["SW-C", "Switch", "Cisco", "X", "", "", "", ""],                    # row 4: no location
                ["", "Switch", "Cisco", "X", "", "", "", ""],                        # row 5: no name
            ]),
            ("Ports", ["Device", "Port"], [["GHOST", "Gi0/1"]]),
        )
        response = preview(client, upload)
        html = page(response)
        assert "There's no location “Nowhere”" in html
        assert "already uses this management IP" in html or "10.10.0.99 is already" in html
        assert "“Toaster” isn't one of" in html and "Location: give a location" in html
        assert "Name is required" in html and "There's no device “GHOST”" in html
        assert 'name="token"' not in html and "Nothing can be imported until the 6 errors are fixed" in html
        assert Device.objects.count() == 1

    def test_rows_clashing_with_each_other(self, as_user, admin):
        """Row 3 takes the rack units row 2 already took; both are new, so only a real dry run catches it."""
        Location.objects.create(name="HQ")
        Rack.objects.create(location=Location.objects.get(), name="Rack 1")
        upload = xlsx(("Devices", ["Name", "Manufacturer", "Model", "Rack", "Position (U)", "Height (U)"],
                       [["SW-1", "Cisco", "X", "Rack 1", "10", "2"], ["SW-2", "Cisco", "X", "Rack 1", "11", "1"]]))
        html = page(preview(as_user(admin), upload))
        assert "Overlaps SW-1" in html and html.count("pill pill-down\">Error") == 1

    def test_port_already_connected(self, as_user, admin):
        client = as_user(admin)
        client.post(reverse("dataio:confirm"), {"token": token_of(preview(client, xlsx(*big_workbook())))})
        upload = xlsx(("Connections", ["Device A", "Port A", "Device B", "Port B", "Function"],
                       [["SW-00", "Gi0/1", "SW-09", "Gi0/1", "Wrong patch"]]))
        html = page(preview(client, upload))
        assert "is already connected" in html

    def test_missing_columns(self, as_user, admin):
        html = page(preview(as_user(admin), xlsx(("Devices", ["Model"], [["Y"]]))))
        assert "Missing column(s): Name" in html  # the key column is always needed
        html = page(preview(as_user(admin), xlsx(("Devices", ["Name", "Model"], [["NEW-1", "Y"]]))))
        assert "would be added — that needs: Manufacturer" in html  # only needed for new records

    def test_unknown_sheet_name(self, as_user, admin):
        html = page(preview(as_user(admin), xlsx(("Stuff", ["Name"], [["X"]]),
                                            ("Locations", ["Name"], [["HQ"]]))))
        assert "Sheet “Stuff”: name it after what it holds" in html


class TestUpdates:
    def test_only_given_columns_change(self, as_user, admin):
        from tests.inventory.helpers import make_device

        device = make_device("SW-CORE-01", mgmt_ip="10.10.0.2", firmware="17.6")
        client = as_user(admin)
        response = preview(client, csv_file(["Name", "Firmware"], [["sw-core-01", "17.9"]]), "devices")
        html = page(response)
        assert "Update" in html and "changes Firmware / OS" in html and "Name" not in html.split("changes")[1][:20]
        client.post(reverse("dataio:confirm"), {"token": token_of(response)})
        device.refresh_from_db()
        assert device.firmware == "17.9" and device.mgmt_ip == "10.10.0.2" and device.rack is not None
        assert device.name == "SW-CORE-01"  # matched case-insensitively, not renamed
        assert AuditLog.objects.filter(action="update", object_repr="SW-CORE-01").exists()

    def test_parent_further_down_the_sheet(self, as_user, admin):
        Location.objects.create(name="HQ")
        upload = csv_file(["Name", "Manufacturer", "Model", "Location", "Parent"],
                          [["CAM-1", "Hik", "X", "HQ", "SW-1"], ["SW-1", "Cisco", "Y", "HQ", ""]])
        client = as_user(admin)
        client.post(reverse("dataio:confirm"), {"token": token_of(preview(client, upload, "devices"))})
        assert Device.objects.get(name="CAM-1").parent.name == "SW-1"

    def test_monitors_ips_and_prefixes(self, as_user, admin):
        client = as_user(admin)
        upload = xlsx(
            ("Prefixes", ["Prefix", "Gateway", "Description"], [["129.205.100.128/28", "129.205.100.129", "Block"]]),
            ("IP addresses", ["Address", "Service"], [["129.205.100.131", "Portal"]]),
            ("Monitors", ["Name", "Type", "Target", "Interval (s)"],
             [["Portal", "HTTP", "https://portal.example", "60"], ["Core", "Ping", "10.0.0.1", ""],
              ["Edge", "ping", "10.0.0.2", ""]]),
        )
        client.post(reverse("dataio:confirm"), {"token": token_of(preview(client, upload))})
        assert Prefix.objects.get().kind == "public" and IPAddress.objects.get().prefix is not None
        assert set(Monitor.objects.values_list("type", flat=True)) == {"http", "ping"}
        assert Monitor.objects.get(name="Core").interval_s == 60


class TestFiles:
    def test_semicolon_csv_and_cell_cleanup(self):
        upload = csv_file(["Name ", "IP"], [["  SW-1 ", "10.0.0.1"], ["", ""]], delimiter=";")
        ((headers, rows),) = read_upload(upload).values()
        assert headers == ["Name", "IP"] and rows == [(2, {"Name": "SW-1", "IP": "10.0.0.1"})]

    @pytest.mark.parametrize("name,content,message", [
        ("notes.txt", b"hello", "Upload a .csv or .xlsx"),
        ("broken.xlsx", b"not a zip", "isn't a readable Excel"),
        ("empty.csv", b"Name\n", "no data rows"),
    ])
    def test_unreadable_files(self, as_user, admin, name, content, message):
        response = preview(as_user(admin), SimpleUploadedFile(name, content))
        assert response.status_code == 400 and message in page(response)

    def test_too_big(self, as_user, admin):
        upload = SimpleUploadedFile("big.csv", b"Name\n" + b"x" * (5 * 1024 * 1024 + 1))
        assert "larger than 5 MB" in page(preview(as_user(admin), upload))


class TestConfirmSafety:
    def test_token_belongs_to_the_session(self, client, as_user, admin):
        Location.objects.create(name="HQ")
        response = preview(as_user(admin), csv_file(["Name", "Address"], [["Branch", ""]], "locations.csv"))
        token = token_of(response)
        from apps.accounts import roles
        from tests.factories import make_user

        other = as_user(make_user(roles.ADMIN, username="admin2"))  # new session for another admin
        other.post(reverse("dataio:confirm"), {"token": token}, follow=True)
        assert not Location.objects.filter(name="Branch").exists()

    def test_bad_token(self, as_user, admin):
        response = as_user(admin).post(reverse("dataio:confirm"), {"token": "../../etc/passwd"}, follow=True)
        assert "expired" in page(response)

    def test_data_changed_since_preview(self, as_user, admin):
        client = as_user(admin)
        Location.objects.create(name="HQ")
        response = preview(client, csv_file(["Name", "Manufacturer", "Model", "Location", "Management IP"],
                                            [["SW-9", "Cisco", "X", "HQ", "10.0.0.9"]]), "devices")
        from tests.inventory.helpers import make_device

        make_device("SNEAKY", mgmt_ip="10.0.0.9")
        result = page(client.post(reverse("dataio:confirm"), {"token": token_of(response)}, follow=True))
        assert "Nothing was imported" in result and not Device.objects.filter(name="SW-9").exists()


class TestExport:
    def test_csv_export_is_logged_and_formula_safe(self, as_user, engineer):
        from tests.inventory.helpers import make_device

        make_device("SW-1", notes="=HYPERLINK(\"http://evil\")")
        response = as_user(engineer).get(reverse("dataio:export", args=["devices", "csv"]))
        text = response.content.decode("utf-8-sig")
        assert response["Content-Disposition"].startswith('attachment; filename="netcore-devices-')
        assert "'=HYPERLINK" in text and "SW-1" in text
        assert AuditLog.objects.filter(action="export", message__startswith="Devices (CSV, 1 rows)").exists()

    def test_formula_safe_round_trip(self):
        data = write_csv(["Notes"], [["=1+1"]])
        ((_h, rows),) = read_upload(SimpleUploadedFile("x.csv", data)).values()
        assert rows[0][1]["Notes"] == "=1+1"

    def test_xlsx_cells_are_text_not_formulas(self):
        book = load_workbook(io.BytesIO(write_xlsx([("Devices", ["Notes"], [["=1+1"]])])))
        cell = book["Devices"]["A2"]
        assert cell.value == "=1+1" and cell.data_type == "s"

    def test_port_map_and_rack_exports(self, as_user, admin):
        client = as_user(admin)
        client.post(reverse("dataio:confirm"), {"token": token_of(preview(client, xlsx(*big_workbook())))})
        sw = Device.objects.get(name="SW-00")
        text = page(client.get(reverse("dataio:export", args=["ports", "csv"]) + f"?device={sw.pk}"))
        lines = text.strip().splitlines()
        assert len(lines) == 11 and "SW-01 · Gi0/1" in lines[1] and "Link 0-1" in lines[1]
        rack = Rack.objects.get(name="Rack 1")
        text = page(client.get(reverse("dataio:export", args=["devices", "csv"]) + f"?rack={rack.pk}"))
        assert len(text.strip().splitlines()) == 6

    def test_viewers_cannot_export(self, as_user, viewer):
        assert as_user(viewer).get(reverse("dataio:export", args=["devices", "csv"])).status_code == 403

    def test_engineers_cannot_import(self, as_user, engineer):
        html = page(as_user(engineer).get(reverse("dataio:data")))
        assert "Preview import" not in html and "Export everything" in html


class TestTemplates:
    def test_excel_template_has_every_sheet_and_a_read_me(self, as_user, admin):
        content = as_user(admin).get(reverse("dataio:template", args=["all", "xlsx"])).content
        book = load_workbook(io.BytesIO(content))
        assert book.sheetnames[0] == "Read me" and "Devices" in book.sheetnames and "Monitors" in book.sheetnames
        assert [c.value for c in book["Devices"][1]][:3] == ["Name", "Purpose", "Type"]

    def test_csv_template_example_imports(self, as_user, admin):
        client = as_user(admin)
        content = client.get(reverse("dataio:template", args=["locations", "csv"])).content
        response = preview(client, SimpleUploadedFile("locations.csv", content))
        client.post(reverse("dataio:confirm"), {"token": token_of(response)})
        assert Location.objects.filter(name="HQ — Server Room").exists()

    def test_unknown_template(self, as_user, admin):
        assert as_user(admin).get(reverse("dataio:template", args=["nope", "csv"])).status_code == 404


def test_report_summary_and_dry_run_flag():
    report = importer.run({"Locations": (["Name"], [(2, {"Name": "HQ"})])})
    assert not report.committed and report.summary() == "Locations: 1 new, 0 updated, 0 unchanged"
    assert not Location.objects.exists()
