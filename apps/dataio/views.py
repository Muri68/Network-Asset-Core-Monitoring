import json
import secrets
import time
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.decorators import capability_required
from apps.audit.log import log_event

from . import importer
from .files import FileError, read_upload, write_csv, write_xlsx
from .resources import RESOURCES

PENDING_DIR = Path(settings.BASE_DIR) / "var" / "imports"
PENDING_MAX_AGE = 3600
SESSION_KEY = "pending_import"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@capability_required("export")
def data(request):
    resources = [(r, r.queryset().count()) for r in RESOURCES.values()]
    return render(request, "system/data.html", {"resources": resources, "active": "data"})


# --------------------------------------------------------------------------
# Import: upload → preview (dry run) → confirm
# --------------------------------------------------------------------------


def _pending_path(token):
    if not token or not token.isalnum():
        return None
    return PENDING_DIR / f"{token}.json"


def _store(request, payload):
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    for old in PENDING_DIR.glob("*.json"):  # tidy up abandoned previews
        if time.time() - old.stat().st_mtime > PENDING_MAX_AGE:
            old.unlink(missing_ok=True)
    token = secrets.token_hex(16)
    _pending_path(token).write_text(json.dumps({**payload, "user": request.user.pk}))
    request.session[SESSION_KEY] = token
    return token


def _load(request, token):
    path = _pending_path(token)
    if token != request.session.get(SESSION_KEY) or path is None or not path.exists():
        return None
    payload = json.loads(path.read_text())
    if payload.get("user") != request.user.pk or time.time() - path.stat().st_mtime > PENDING_MAX_AGE:
        return None
    return payload


def _sheets(payload):
    return {title: (headers, [tuple(r) for r in rows]) for title, (headers, rows) in payload["sheets"].items()}


@capability_required("import")
@require_POST
def preview(request):
    upload = request.FILES.get("file")
    chosen = request.POST.get("resource", "")
    chosen = chosen if chosen in RESOURCES else None
    ctx = {"chosen": chosen}
    if upload is None:
        ctx["file_error"] = "Choose a .csv or .xlsx file first."
        return render(request, "system/partials/import_preview.html", ctx, status=400)
    try:
        sheets = read_upload(upload)
    except FileError as e:
        ctx["file_error"] = str(e)
        return render(request, "system/partials/import_preview.html", ctx, status=400)
    report = importer.run(sheets, chosen=chosen, commit=False, filename=upload.name)
    token = None
    if not report.errors and report.changes:
        token = _store(request, {"filename": upload.name, "chosen": chosen, "sheets": sheets})
    return render(request, "system/partials/import_preview.html",
                  {"report": report, "filename": upload.name, "token": token})


@capability_required("import")
@require_POST
def confirm(request):
    payload = _load(request, request.POST.get("token", ""))
    if payload is None:
        messages.error(request, "That preview has expired. Upload the file again.")
        return redirect("dataio:data")
    report = importer.run(_sheets(payload), chosen=payload["chosen"], commit=True, filename=payload["filename"])
    _pending_path(request.POST["token"]).unlink(missing_ok=True)
    request.session.pop(SESSION_KEY, None)
    if report.committed:
        messages.success(request, f"Imported {payload['filename']} — {report.summary()}.")
    else:
        messages.error(request, "Nothing was imported: the data changed since the preview and some rows now have "
                                "errors. Upload the file again to see them.")
    return redirect("dataio:data")


# --------------------------------------------------------------------------
# Templates and exports
# --------------------------------------------------------------------------


def _download(content, filename, content_type):
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _readme():
    lines = [["NetCore import template"], [],
             ["Fill in the sheets you need and delete the example rows. Sheets are imported in this order;"],
             ["rows are matched by name / address, so importing again updates instead of duplicating."],
             ["Empty cells in an existing record clear that field; columns you leave out keep their value."], [],
             ["Sheet", "Column", "What to put in it", "Example"]]
    for r in RESOURCES.values():
        for c in r.columns:
            if not c.export_only:
                lines.append([r.label, c.header + (" *" if c.required else ""), c.help, c.example])
    return [("Read me", lines)]


@capability_required("export")
def template(request, key, fmt):
    if fmt == "xlsx" and key == "all":
        sheets = [(r.label, r.import_headers, [[c.example for c in r.columns if not c.export_only]])
                  for r in RESOURCES.values()]
        return _download(write_xlsx(sheets, notes=_readme()), "netcore-import-template.xlsx", XLSX)
    resource = RESOURCES.get(key)
    if resource is None or fmt != "csv":
        raise Http404
    example = [c.example for c in resource.columns if not c.export_only]
    return _download(write_csv(resource.import_headers, [example]), f"netcore-{key}-template.csv",
                     "text/csv; charset=utf-8")


FILTERS = {"devices": ["rack"], "ports": ["device"]}


@capability_required("export")
def export(request, key, fmt):
    if fmt not in ("csv", "xlsx"):
        raise Http404
    stamp = timezone.localdate().isoformat()
    if key == "all":
        if fmt != "xlsx":
            raise Http404
        sheets = [(r.label, r.headers, [list(r.values(o).values()) for o in r.queryset()]) for r in RESOURCES.values()]
        log_event("export", message=f"Everything (Excel, {sum(len(s[2]) for s in sheets)} rows)")
        return _download(write_xlsx(sheets), f"netcore-export-{stamp}.xlsx", XLSX)
    resource = RESOURCES.get(key)
    if resource is None:
        raise Http404
    filters = {f: request.GET[f] for f in FILTERS.get(key, []) if request.GET.get(f, "").isdigit()}
    rows = [[resource.values(o)[h] for h in resource.headers] for o in resource.queryset(**filters)]
    scope = f" ({', '.join(f'{k} #{v}' for k, v in filters.items())})" if filters else ""
    log_event("export", message=f"{resource.label}{scope} ({fmt.upper()}, {len(rows)} rows)")
    name = f"netcore-{key}-{stamp}.{fmt}"
    if fmt == "csv":
        return _download(write_csv(resource.headers, rows), name, "text/csv; charset=utf-8")
    return _download(write_xlsx([(resource.label, resource.headers, rows)]), name, XLSX)
