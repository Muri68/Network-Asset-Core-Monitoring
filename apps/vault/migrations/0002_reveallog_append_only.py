"""The reveal log can be added to, never changed or deleted (PLAN.md §6.7), like the audit log."""

from django.db import migrations

SQLITE = [
    """CREATE TRIGGER vault_reveallog_no_update BEFORE UPDATE ON vault_reveallog
       BEGIN SELECT RAISE(ABORT, 'reveal log is append-only'); END;""",
    """CREATE TRIGGER vault_reveallog_no_delete BEFORE DELETE ON vault_reveallog
       BEGIN SELECT RAISE(ABORT, 'reveal log is append-only'); END;""",
]
SQLITE_REVERSE = [
    "DROP TRIGGER IF EXISTS vault_reveallog_no_update;",
    "DROP TRIGGER IF EXISTS vault_reveallog_no_delete;",
]

POSTGRES = [
    """CREATE OR REPLACE FUNCTION vault_reveallog_append_only() RETURNS trigger AS $$
       BEGIN RAISE EXCEPTION 'reveal log is append-only'; END;
       $$ LANGUAGE plpgsql;""",
    """CREATE TRIGGER vault_reveallog_no_change BEFORE UPDATE OR DELETE ON vault_reveallog
       FOR EACH ROW EXECUTE FUNCTION vault_reveallog_append_only();""",
    """CREATE TRIGGER vault_reveallog_no_truncate BEFORE TRUNCATE ON vault_reveallog
       FOR EACH STATEMENT EXECUTE FUNCTION vault_reveallog_append_only();""",
]
POSTGRES_REVERSE = [
    "DROP TRIGGER IF EXISTS vault_reveallog_no_change ON vault_reveallog;",
    "DROP TRIGGER IF EXISTS vault_reveallog_no_truncate ON vault_reveallog;",
    "DROP FUNCTION IF EXISTS vault_reveallog_append_only();",
]


def _run(statements_by_vendor):
    def run(apps, schema_editor):
        for sql in statements_by_vendor.get(schema_editor.connection.vendor, []):
            schema_editor.execute(sql)

    return run


class Migration(migrations.Migration):
    dependencies = [("vault", "0001_initial")]

    operations = [
        migrations.RunPython(
            _run({"sqlite": SQLITE, "postgresql": POSTGRES}),
            _run({"sqlite": SQLITE_REVERSE, "postgresql": POSTGRES_REVERSE}),
        )
    ]
