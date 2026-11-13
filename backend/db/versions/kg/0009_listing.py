"""kg: the vetted writer of the listing columns (folder, hide_children, alien) of the records, and the indexes a listing reads"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index
from db.lib.run_sql import run_sql
from db.lib.vetted import run_vetted

revision = "kg_0009"
down_revision = "kg_0008"
branch_labels = None
depends_on = None
older_code_compatible = "adds a function and indexes only; older code does not call the function and an index changes no answer"

WRITERS = ("w_listing_set",)

# The listing indexes: one connector's live listing rows in each sort order (the ordering and tie-break of the listing, in both directions), so that a window of a page is a range of an index.
# Partial on exactly what a listing reads (live, no placeholder, not another organisation's), so that the predicate of the query implies it. A previous page reads the same index backwards.
LIVE = "kind IN (2, 3) AND NOT deleted AND NOT placeholder AND NOT alien"
SORT_KEYS = {"name": "name_sort", "updated": "updated_ms", "created": "created_ms", "size": "size_bytes"}
INDEXES = [
    *((f"records_list_{k}_{d}", f'org_id, app_sk, {col} {d.upper()} NULLS LAST, (id COLLATE "C")', LIVE) for k, col in SORT_KEYS.items() for d in ("asc", "desc")),
    ("records_list_class", "org_id, app_sk, class_id, sk", LIVE),      # the rows of one class of a connector: a count by what a viewer's grants open
    ("records_hiders", "org_id, app_sk", "hide_children"),          # whether a connector has a group that hides its children: asked by every listing, never a scan of the connector
]


def upgrade():
    run_sql(__file__)
    # Built inside the upgrade's one transaction, so the writers of kg.records wait for each build (as graph_0011 does).
    # shortcut: a build reads all of kg.records with its writers blocked: run this upgrade on an install whose typed graph is large in a maintenance window.
    for name, cols, where in INDEXES:
        ensure_partitioned_index(op.get_bind(), "kg.records", name, cols, where=where, blocking=True)
    run_vetted(__file__, *WRITERS)


def downgrade():
    # Exactly what this revision added, by name: the writer, its pin and registry row. The write grants of the vetted role are then recomputed, for the table the pin named, from the
    # pins that remain (kernel._sync_vetted_privs); the function is dropped without cascade.
    names = ", ".join(f"'{n}'" for n in WRITERS)
    op.execute(f"""
DO $$
DECLARE
  mine oid[];
  rels regclass[];
  t regclass;
  f oid;
BEGIN
  SELECT coalesce(array_agg(oid), '{{}}') INTO mine FROM pg_proc WHERE pronamespace = 'kg'::regnamespace AND proname IN ({names});
  SELECT coalesce(array_agg(DISTINCT rel), '{{}}') INTO rels FROM kernel.vetted_write_pins WHERE proc::oid = ANY(mine);
  DELETE FROM kernel.vetted_write_pins WHERE proc::oid = ANY(mine);
  DELETE FROM kernel.function_registry WHERE proc::oid = ANY(mine);
  FOREACH t IN ARRAY rels LOOP
    PERFORM kernel._sync_vetted_privs(t);
  END LOOP;
  FOREACH f IN ARRAY mine LOOP
    EXECUTE format('DROP FUNCTION %s RESTRICT', f::regprocedure);
  END LOOP;
END $$""")
    for name, *_ in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS kg.{name}")
    op.execute("ALTER TABLE kg.records DROP COLUMN name_sort")
