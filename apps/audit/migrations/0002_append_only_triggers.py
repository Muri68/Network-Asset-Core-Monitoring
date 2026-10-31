"""Database-level guarantee that audit rows are never changed or deleted (PLAN.md §6.8)."""

from django.db import migrations

SQLITE = [
    """CREATE TRIGGER audit_auditlog_no_update BEFORE UPDATE ON audit_auditlog
       BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;""",
    """CREATE TRIGGER audit_auditlog_no_delete BEFORE DELETE ON audit_auditlog
       BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;""",
]
SQLITE_REVERSE = [
    "DROP TRIGGER IF EXISTS audit_auditlog_no_update;",
    "DROP TRIGGER IF EXISTS audit_auditlog_no_delete;",
]

POSTGRES = [
    """CREATE OR REPLACE FUNCTION audit_auditlog_append_only() RETURNS trigger AS $$
       BEGIN RAISE EXCEPTION 'audit log is append-only'; END;
       $$ LANGUAGE plpgsql;""",
    """CREATE TRIGGER audit_auditlog_no_change BEFORE UPDATE OR DELETE ON audit_auditlog
       FOR EACH ROW EXECUTE FUNCTION audit_auditlog_append_only();""",
    """CREATE TRIGGER audit_auditlog_no_truncate BEFORE TRUNCATE ON audit_auditlog
       FOR EACH STATEMENT EXECUTE FUNCTION audit_auditlog_append_only();""",
]
POSTGRES_REVERSE = [
    "DROP TRIGGER IF EXISTS audit_auditlog_no_change ON audit_auditlog;",
    "DROP TRIGGER IF EXISTS audit_auditlog_no_truncate ON audit_auditlog;",
    "DROP FUNCTION IF EXISTS audit_auditlog_append_only();",
]


def _run(statements_by_vendor):
    def run(apps, schema_editor):
        for sql in statements_by_vendor.get(schema_editor.connection.vendor, []):
            schema_editor.execute(sql)

    return run


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]

    operations = [
        migrations.RunPython(
            _run({"sqlite": SQLITE, "postgresql": POSTGRES}),
            _run({"sqlite": SQLITE_REVERSE, "postgresql": POSTGRES_REVERSE}),
        )
    ]
