"""
Reading uploaded CSV / Excel files into plain rows, and writing exports (PLAN.md §7.6).

Rows are {header: text} dicts with the spreadsheet row number, so errors can point at "row 7".
Exports are protected against formula injection: a cell like "=HYPERLINK(...)" is never a formula.
"""

import csv
import datetime
import io
from decimal import Decimal

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5000
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


class FileError(Exception):
    """The file as a whole can't be read (wrong type, too big, no headers…)."""


def normalise_header(text):
    return " ".join(str(text or "").replace("_", " ").split()).lower()


def cell_text(value):
    """A spreadsheet cell as text: 24.0 → "24", dates → ISO, None → ""."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime.datetime):
        return value.date().isoformat() if value.time() == datetime.time.min else value.isoformat(sep=" ")
    if isinstance(value, datetime.date):
        return value.isoformat()
    text = str(value).strip()
    # Undo our own CSV export protection ('=…) so a round trip gives back the same value.
    if text.startswith("'") and text[1:2] in FORMULA_START and text[1:2]:
        text = text[1:]
    return text


def _rows(table):
    """[(row number, {header: text})] from a list of raw rows; the first non-empty row is the header."""
    table = [list(r) for r in table]
    start = next((i for i, r in enumerate(table) if any(cell_text(c) for c in r)), None)
    if start is None:
        return [], []
    headers = [cell_text(h) for h in table[start]]
    rows = []
    for offset, raw in enumerate(table[start + 1:], start=start + 2):
        values = [cell_text(c) for c in raw]
        if not any(values):
            continue
        rows.append((offset, {h: (values[i] if i < len(values) else "") for i, h in enumerate(headers) if h}))
    return [h for h in headers if h], rows


def read_upload(upload):
    """{sheet name: (headers, rows)} from an uploaded .csv or .xlsx file."""
    name = (upload.name or "").lower()
    if upload.size > MAX_BYTES:
        raise FileError("The file is larger than 5 MB. Split it into smaller files.")
    data = upload.read()
    if name.endswith(".xlsx"):
        try:
            book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as e:  # noqa: BLE001 — any parse failure means "not a usable workbook"
            raise FileError("This isn't a readable Excel (.xlsx) file.") from e
        sheets = {ws.title: _rows(ws.iter_rows(values_only=True)) for ws in book.worksheets}
        book.close()
    elif name.endswith(".csv"):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")  # Excel's "CSV" on Windows
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        sheets = {upload.name.rsplit(".", 1)[0]: _rows(csv.reader(io.StringIO(text), dialect))}
    else:
        raise FileError("Upload a .csv or .xlsx file.")
    sheets = {k: v for k, v in sheets.items() if v[1]}
    if not sheets:
        raise FileError("The file has no data rows under the header row.")
    if sum(len(rows) for _h, rows in sheets.values()) > MAX_ROWS:
        raise FileError(f"More than {MAX_ROWS} rows. Split the file into smaller ones.")
    return sheets


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def _safe_csv(value):
    if isinstance(value, str) and value.startswith(FORMULA_START):
        return "'" + value
    return value


def write_csv(headers, rows):
    out = io.StringIO()
    out.write("﻿")  # BOM: Excel then opens UTF-8 (names with accents, "—") correctly
    writer = csv.writer(out)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_safe_csv(v) for v in row])
    return out.getvalue().encode("utf-8")


HEADER_FONT = Font(bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor="1F2937")


def write_xlsx(sheets, notes=None):
    """sheets: [(title, headers, rows)]; notes: optional [(title, [[cells]])] sheets put first."""
    book = Workbook()
    book.remove(book.active)
    for title, lines in notes or []:
        ws = book.create_sheet(title)
        for line in lines:
            ws.append(line)
        ws.column_dimensions["A"].width = 22
        ws.column_dimensions["B"].width = 26
        ws.column_dimensions["C"].width = 70
        ws.column_dimensions["D"].width = 30
    for title, headers, rows in sheets:
        ws = book.create_sheet(title[:31])
        ws.append(headers)
        for cell in ws[1]:
            cell.font, cell.fill = HEADER_FONT, HEADER_FILL
        for row in rows:
            ws.append([v if not isinstance(v, Decimal) else float(v) for v in row])
            for cell in ws[ws.max_row]:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    cell.data_type = "s"  # text, never a formula
        ws.freeze_panes = "A2"
        for i, header in enumerate(headers, start=1):
            longest = max([len(str(header))] + [len(str(r[i - 1] or "")) for r in rows[:200]])
            ws.column_dimensions[get_column_letter(i)].width = min(max(longest + 2, 10), 50)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()
