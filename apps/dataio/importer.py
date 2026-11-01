"""
Running an import (PLAN.md §7.6): preview and the real thing are the same code.

Everything happens inside one transaction. A preview (dry run) really saves each row — so a row can refer
to one above it, and two rows claiming the same rack U clash — and is then rolled back. The real import is
only committed when no row has an error; otherwise it's rolled back too and nothing changes.
"""

from dataclasses import dataclass, field

from django.db import transaction

from apps.audit.log import log_event

from .files import normalise_header
from .resources import RESOURCES, Missing, RowError, for_sheet


@dataclass
class RowResult:
    row: int
    action: str  # create / update / unchanged / error
    name: str
    detail: str = ""


@dataclass
class SheetResult:
    resource: object
    sheet: str
    rows: list = field(default_factory=list)
    ignored_columns: list = field(default_factory=list)

    def count(self, action):
        return sum(1 for r in self.rows if r.action == action)

    @property
    def created(self):
        return self.count("create")

    @property
    def updated(self):
        return self.count("update")

    @property
    def unchanged(self):
        return self.count("unchanged")

    @property
    def errors(self):
        return self.count("error")


@dataclass
class Report:
    sheets: list
    committed: bool = False
    problems: list = field(default_factory=list)  # file-level: unknown sheets, missing columns

    @property
    def errors(self):
        return sum(s.errors for s in self.sheets) + len(self.problems)

    @property
    def changes(self):
        return sum(s.created + s.updated for s in self.sheets)

    def summary(self):
        parts = [f"{s.resource.label}: {s.created} new, {s.updated} updated, {s.unchanged} unchanged"
                 for s in self.sheets]
        return "; ".join(parts) or "nothing"


class _Rollback(Exception):
    pass


def match_sheets(sheets, chosen=None):
    """[(resource, sheet title, headers, rows)] in import order, plus file-level problems."""
    matched, problems = [], []
    for title, (headers, rows) in sheets.items():
        resource = for_sheet(title)
        if resource is None and chosen and len(sheets) == 1:
            resource = RESOURCES[chosen]
        if resource is None:
            names = ", ".join(r.label for r in RESOURCES.values())
            problems.append(f"Sheet “{title}”: name it after what it holds ({names}), or choose the type above.")
            continue
        matched.append((resource, title, headers, rows))
    matched.sort(key=lambda m: m[0].order)
    return matched, problems


def _errors(form):
    """'Field label: message' for every form error."""
    out = []
    for name, errors in form.errors.items():
        label = form.fields[name].label if name in form.fields else ""
        for e in errors:
            out.append(f"{label}: {e}" if label and name != "__all__" else str(e))
    return "; ".join(out)


def _describe_changes(form):
    names = [str(form.fields[n].label or n) for n in form.changed_data if n in form.fields]
    return "changes " + ", ".join(names) if names else ""


def _import_sheet(resource, title, headers, rows):
    result = SheetResult(resource, title)
    by_norm = {normalise_header(h): h for h in resource.import_headers}
    present = {}
    for h in headers:
        key = normalise_header(h)
        if key in by_norm:
            present[by_norm[key]] = h
        elif key not in {normalise_header(c.header) for c in resource.columns}:
            result.ignored_columns.append(h)
    missing = [h for h in resource.keys if h not in present]
    if missing:
        result.rows.append(RowResult(1, "error", "Header row", f"Missing column(s): {', '.join(missing)}."))
        return result

    def attempt(number, raw, last_try):
        given = {col: raw.get(src, "").strip() for col, src in present.items()}
        name = resource.name({**dict.fromkeys(resource.headers, ""), **given})
        try:
            for c in resource.columns:
                if c.required and c.header in given and not given[c.header]:
                    raise RowError(f"{c.header} is required.")
            obj = resource.find({**dict.fromkeys(resource.headers, ""), **given})
            if obj is None:
                needed = [c.header for c in resource.columns if c.required and not given.get(c.header)]
                if needed:
                    raise RowError(f"Not found, so it would be added — that needs: {', '.join(needed)}.")
            if obj is not None:
                current = resource.values(obj)
                values = {**current, **given}  # columns not in the file keep their value
                # Matching ignores case ("sw-core-01" finds SW-CORE-01); don't rename it to the file's spelling.
                values.update({k: current[k] for k in resource.keys})
            else:
                # New record: empty cells and missing columns get the defaults.
                values = {**dict.fromkeys(resource.headers, ""), **resource.defaults(),
                          **{k: v for k, v in given.items() if v}}
            with transaction.atomic():  # a failing row leaves no half-saved records behind
                form = resource.form(values, obj)
                if not form.is_valid():
                    return RowResult(number, "error", name, _errors(form))
                if obj is not None and not form.has_changed():
                    return RowResult(number, "unchanged", name)
                detail = _describe_changes(form) if obj is not None else ""
                resource.save(form)
            return RowResult(number, "update" if obj is not None else "create", name, detail)
        except Missing as e:
            if not last_try:
                return None  # may be created further down this sheet: try again at the end
            return RowResult(number, "error", name, str(e))
        except RowError as e:
            return RowResult(number, "error", name, str(e))

    deferred = []
    for number, raw in rows:
        outcome = attempt(number, raw, last_try=False)
        if outcome is None:
            deferred.append((number, raw))
        else:
            result.rows.append(outcome)
    for number, raw in deferred:
        result.rows.append(attempt(number, raw, last_try=True))
    result.rows.sort(key=lambda r: r.row)
    return result


def run(sheets, *, chosen=None, commit=False, filename=""):
    """Import parsed `sheets` ({title: (headers, rows)}). Returns a Report; saves only if commit and no errors."""
    matched, problems = match_sheets(sheets, chosen)
    report = Report([], problems=problems)
    try:
        with transaction.atomic():
            for resource, title, headers, rows in matched:
                report.sheets.append(_import_sheet(resource, title, headers, rows))
            if not commit or report.errors:
                raise _Rollback
            log_event("import", message=f"{filename}: {report.summary()}"[:500])
            report.committed = True
    except _Rollback:
        pass
    return report
