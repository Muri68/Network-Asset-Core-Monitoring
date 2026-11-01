"""
Rack connection map (the owner's "network Mapping" spreadsheet): one sheet per rack, one row per device from
the top U down, with every connected port listed as "a. Port 4 to FW-201E port3", the device's purpose and
its notes as remark. Made for reading and printing, not for importing (use the Connections export for that).
"""

import io
import re
import string

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.properties import PageSetupProperties

from apps.dcim.models import Port
from apps.inventory.models import Rack

HEADERS = ["SERIAL", "RACK NUMBER", "RACK UNIT", "DEVICE", "CONNECTED TO", "PURPOSE", "REMARK"]
WIDTHS = [8, 14, 11, 30, 52, 40, 24]
CHARS_PER_LINE = {4: 46, 5: 36, 6: 22}  # rough wrap widths (column → characters) for row heights

THIN = Side(style="thin", color="000000")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEADER_FILL = PatternFill("solid", fgColor="D9E2F3")
BOLD = InlineFont(b=True)
PLAIN = InlineFont()


def port_label(port):
    """'7' → 'Port 7', 'port4' → 'Port 4'; named interfaces (eth0, Gi1/0/4, WAN2) stay as they are."""
    name = port.name.strip()
    if name.isdigit():
        return f"Port {name}"
    if re.match(r"(?i)^port\s*\d", name):
        return "Port " + re.sub(r"(?i)^port\s*", "", name)
    return name


def _letter(i):
    return string.ascii_lowercase[i] if i < 26 else str(i + 1)


def _where(device, rack):
    """' on RACK 2 Unit 36' (or 'Unit 21 - 22') when the other device is in another rack."""
    if device.rack_id and device.rack_id != rack.pk:
        units = str(device.position_u) if device.height_u <= 1 else f"{device.position_u} - {device.top_u}"
        return f" on {device.rack.name.upper()} Unit {units}"
    return ""


def connection_lines(device, rack):
    """[(letter, port text, trunk?, rest)] for each connected port of `device`, in port order."""
    ports = (Port.objects.filter(device=device)
             .select_related("connection_a__b_port__device__rack", "connection_a__b_external",
                             "connection_b__a_port__device__rack")
             .order_by("sort_key"))
    lines = []
    for port in ports:
        conn = getattr(port, "connection_a", None) or getattr(port, "connection_b", None)
        if conn is None:
            continue
        other = conn.b_port if conn.a_port_id == port.pk else conn.a_port
        if other is not None:
            target = f"{other.device.name} {port_label(other)}{_where(other.device, rack)}"
        else:
            target = str(conn.b_external)
        rest = f" to {target}" + (f" ({conn.function})" if conn.function else "")
        lines.append((port_label(port), port.mode == Port.Mode.TRUNK, rest))
    return [(f"{_letter(i)}.", *line) for i, line in enumerate(lines)]


def _rich(lines):
    """The 'Connected to' cell: bold letters and TRUNK, one connection per line."""
    blocks = []
    for i, (letter, port, trunk, rest) in enumerate(lines):
        blocks.append(TextBlock(BOLD, ("\n" if i else "") + letter + " "))
        blocks.append(TextBlock(PLAIN, port))
        if trunk:
            blocks.append(TextBlock(BOLD, " TRUNK"))
        blocks.append(TextBlock(PLAIN, rest))
    return CellRichText(*blocks)


def _lines_needed(text, column):
    width = CHARS_PER_LINE[column]
    return sum(max(1, -(-len(part) // width)) for part in str(text).split("\n")) if text else 1


def unit_label(device):
    return str(device.position_u) if device.height_u <= 1 else f"{device.top_u}–{device.position_u}"


def _sheet(book, rack, title):
    ws = book.create_sheet(title[:31])
    ws.append([f"{rack.name.upper()} — {rack.location}"])
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(HEADERS))
    ws["A1"].font = Font(bold=True, size=14)
    ws["A1"].alignment = Alignment(horizontal="center")
    ws.append(HEADERS)
    for col, cell in enumerate(ws[2], start=1):
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL
        cell.border = BORDER
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[cell.column_letter].width = WIDTHS[col - 1]

    devices = list(rack.devices.exclude(status="retired").select_related("device_model").order_by("-position_u"))
    first = 3
    for serial, device in enumerate(devices, start=1):
        row = first + serial - 1
        lines = connection_lines(device, rack)
        ws.cell(row, 1, serial)
        ws.cell(row, 3, unit_label(device))
        ws.cell(row, 4, device.name)
        ws.cell(row, 7, device.notes)
        if lines or device.purpose:
            ws.cell(row, 5, _rich(lines) if lines else "")
            ws.cell(row, 6, device.purpose)
        else:
            ws.cell(row, 5, "Not in use")
            ws.merge_cells(start_row=row, start_column=5, end_row=row, end_column=6)
        plain = "\n".join(f"{a} {b}{' TRUNK' if t else ''}{c}" for a, b, t, c in lines)
        height = max(_lines_needed(plain, 5), _lines_needed(device.purpose, 6), _lines_needed(device.notes, 6),
                     _lines_needed(device.name, 4))
        ws.row_dimensions[row].height = max(20, 15 * height + 4)
        for col in range(1, len(HEADERS) + 1):
            cell = ws.cell(row, col)
            cell.border = BORDER
            centered = col in (1, 2, 3) or (col == 5 and not lines and not device.purpose)
            cell.alignment = Alignment(vertical="center", wrap_text=True,
                                       horizontal="center" if centered else "left")
    if devices:
        last = first + len(devices) - 1
        ws.cell(first, 2, rack.name.replace("Rack", "").strip() or rack.name)
        if last > first:
            ws.merge_cells(start_row=first, start_column=2, end_row=last, end_column=2)
    else:
        ws.cell(first, 1, "No devices in this rack yet.")

    ws.freeze_panes = "A3"
    ws.print_title_rows = "2:2"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.oddHeader.center.text = f"{rack.name} — connection map"
    ws.oddFooter.left.text = f"NetCore · {timezone.localdate():%d %b %Y}"
    ws.oddFooter.right.text = "Page &P of &N"
    return ws


def workbook(racks):
    book = Workbook()
    book.remove(book.active)
    used = set()
    for rack in racks:
        title = rack.name if rack.name not in used else f"{rack.name} ({rack.location})"
        used.add(title)
        _sheet(book, rack, title)
    if not book.worksheets:
        book.create_sheet("No racks").append(["No racks recorded yet."])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def all_racks():
    return Rack.objects.select_related("location").order_by("location__name", "name")
