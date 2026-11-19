"""kg: the restamp queue, the mark that hides a record until it is restamped, and the vetted writers of both"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index
from db.lib.run_sql import run_sql
from db.lib.vetted import run_vetted

revision = "kg_0006"
down_revision = "kg_0005"
branch_labels = None
depends_on = None
older_code_compatible = "adds a table, a nullable column nothing older reads, a sequence and functions; older code never touches them"

WRITERS = ("w_fence_rows", "w_queue_add", "w_queue_touch", "w_queue_done")


def upgrade():
    run_sql(__file__)
    # The marks of one job, in the order the batches take them. Built plain (a checked upgrade is one transaction): kg holds rows only where the projection has been switched on.
    ensure_partitioned_index(op.get_bind(), "kg.records", "records_fence", "org_id, fence_job, sk", unique=False, where="fence_job IS NOT NULL", blocking=True)
    run_vetted(__file__, *WRITERS)


def downgrade():
    # Exactly what this revision added, by name. The writers go first (their pins and registry rows, then the write grants of the vetted role are recomputed for the tables the pins
    # named, so a grant another function's pin needs stays), then the table, the column (and with it its index) and the sequence.
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
  SELECT coalesce(array_agg(DISTINCT rel), '{{}}') INTO rels FROM kernel.vetted_write_pins WHERE proc::oid = ANY(mine) AND rel <> 'kg.restamp_queue'::regclass;
  DELETE FROM kernel.vetted_write_pins WHERE proc::oid = ANY(mine);
  DELETE FROM kernel.function_registry WHERE proc::oid = ANY(mine);
  FOREACH f IN ARRAY mine LOOP
    EXECUTE format('DROP FUNCTION %s RESTRICT', f::regprocedure);
  END LOOP;
  DELETE FROM kernel.rls_registry WHERE rel = 'kg.restamp_queue'::regclass;
  DROP TABLE kg.restamp_queue;
  FOREACH t IN ARRAY rels LOOP
    PERFORM kernel._sync_vetted_privs(t);
  END LOOP;
END $$""")
    op.execute("ALTER TABLE kg.records DROP COLUMN fence_job")
    op.execute("DROP SEQUENCE kg.job_id_seq")
