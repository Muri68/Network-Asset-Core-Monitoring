"""
A port may be in at most one connection (PLAN.md §6.3).

The unique columns already stop a port appearing twice as side A or twice as side B;
these triggers stop it being side A of one cable and side B of another.
"""

from django.db import migrations

CHECK = """EXISTS (SELECT 1 FROM dcim_connection c WHERE c.id <> NEW.id
           AND (c.a_port_id = NEW.b_port_id OR c.b_port_id = NEW.a_port_id))"""

SQLITE = [
    f"""CREATE TRIGGER dcim_connection_one_per_port_insert BEFORE INSERT ON dcim_connection
        WHEN {CHECK.replace("c.id <> NEW.id", "1 = 1")}
        BEGIN SELECT RAISE(ABORT, 'port is already connected'); END;""",
    f"""CREATE TRIGGER dcim_connection_one_per_port_update BEFORE UPDATE ON dcim_connection
        WHEN {CHECK}
        BEGIN SELECT RAISE(ABORT, 'port is already connected'); END;""",
]
SQLITE_REVERSE = [
    "DROP TRIGGER IF EXISTS dcim_connection_one_per_port_insert;",
    "DROP TRIGGER IF EXISTS dcim_connection_one_per_port_update;",
]

POSTGRES = [
    f"""CREATE OR REPLACE FUNCTION dcim_connection_one_per_port() RETURNS trigger AS $$
        BEGIN
          IF {CHECK.replace("c.id <> NEW.id", "c.id IS DISTINCT FROM NEW.id")} THEN
            RAISE EXCEPTION 'port is already connected';
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;""",
    """CREATE TRIGGER dcim_connection_one_per_port BEFORE INSERT OR UPDATE ON dcim_connection
       FOR EACH ROW EXECUTE FUNCTION dcim_connection_one_per_port();""",
]
POSTGRES_REVERSE = [
    "DROP TRIGGER IF EXISTS dcim_connection_one_per_port ON dcim_connection;",
    "DROP FUNCTION IF EXISTS dcim_connection_one_per_port();",
]


def _run(statements_by_vendor):
    def run(apps, schema_editor):
        for sql in statements_by_vendor.get(schema_editor.connection.vendor, []):
            schema_editor.execute(sql)

    return run


class Migration(migrations.Migration):
    dependencies = [("dcim", "0001_initial")]

    operations = [
        migrations.RunPython(
            _run({"sqlite": SQLITE, "postgresql": POSTGRES}),
            _run({"sqlite": SQLITE_REVERSE, "postgresql": POSTGRES_REVERSE}),
        )
    ]
