"""graph: vetted reads and indexes of the knowledge graph's per-record lookups (records of a connector by id, organisations by name, persons by source key,
principals by email, people by name), which row-level security cannot serve from the indexes of a plain statement"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index
from db.lib.vetted import run_vetted

revision = "graph_0006"
down_revision = "graph_0005"
branch_labels = None
depends_on = None
older_code_compatible = "adds indexes and functions only; older code does not call them and reads the same rows"

FUNCTIONS = {"records_page_by_connector": "text, text, int", "organizations_by_name": "text, text[]", "person_by_source_key": "text",
             "principals_by_email": "text[], text[]", "people_by_name": "text, int"}

# The full name as the provider normalizes it for a search: lower-cased, split on blanks, empty words dropped.
# (array_to_string is not immutable, so the same split and rejoin is spelled as a trim and a collapse of blanks.)
NAME = "btrim(regexp_replace(lower(coalesce(doc->>'fullName', '')), ' +', ' ', 'g'), ' ')"
PARTS = "btrim(regexp_replace(lower(coalesce(doc->>'firstName', '') || ' ' || coalesce(doc->>'lastName', '')), ' +', ' ', 'g'), ' ')"
# (name, columns, predicate, access method)
INDEXES = [
    ("nodes_record_connector_c", "org_id, (doc->>'connectorId'), (id COLLATE ucs_basic)", "coll = 'records'", ""),
    ("nodes_organization_name", "org_id, (doc->>'normalizedName')", "coll = 'organizations'", ""),
    ("nodes_person_source_key", "org_id, (doc->>'sourceKey')", "coll = 'person'", ""),
    ("nodes_group_email", "org_id, lower(doc->>'email')", "coll = 'groups'", ""),
    ("nodes_person_email", "org_id, lower(doc->>'email')", "coll = 'person'", ""),
    ("nodes_person_name", f"org_id, ({NAME})", "coll = 'person'", ""),
    ("nodes_person_parts", f"org_id, ({PARTS})", "coll = 'person'", ""),
    ("nodes_person_name_trgm", f"({NAME}) ext.gin_trgm_ops", "coll = 'person'", "gin"),
]


def upgrade():
    # Built inside the upgrade's one transaction, so the writers of a leaf wait for its build (as graph_0002 does).
    # shortcut: the leaf locks last until the migration commits, and a build reads the whole leaf (about 8 s for 1.2M rows, tens of minutes for 100M, writers blocked):
    # run this upgrade on a graph that holds many rows in a maintenance window. No release holds a graph built before these revisions.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA ext")
    for name, cols, where, using in INDEXES:
        ensure_partitioned_index(op.get_bind(), "graph.nodes", name, cols, where=where, blocking=True, using=using)
    run_vetted(__file__, *FUNCTIONS)


def downgrade():
    for name, args in FUNCTIONS.items():
        op.execute(f"DROP FUNCTION IF EXISTS graph.{name}({args})")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
    for name, *_ in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS graph.{name}")
