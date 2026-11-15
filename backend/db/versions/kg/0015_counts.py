"""kg: the counters of the typed graph (kg.counts), the trigger on kg.records that adds their deltas, and the vetted writers of the fold"""
from alembic import op
from db.lib.run_sql import run_sql
from db.lib.vetted import run_vetted

revision = "kg_0015"
down_revision = "kg_0014"
branch_labels = None
depends_on = None
older_code_compatible = ("adds a table, its sequence, functions and a trigger on kg.records that only inserts into that table, as the writer of kg.records (whose pins give "
                         "it the insert); older code never reads the table and writes kg.records as before")

WRITERS = ("w_counts_add", "w_counts_delete")


def upgrade():
    run_sql(__file__)
    run_vetted(__file__, *WRITERS)


def downgrade():
    # Exactly what this revision added, by name: the triggers, their functions and exemption, the writers (pins and registry rows), the table and its sequence.
    op.execute("DROP TRIGGER kg_records_count_insert ON kg.records")
    op.execute("DROP TRIGGER kg_records_count_update ON kg.records")
    op.execute("DROP TRIGGER kg_records_count_delete ON kg.records")
    op.execute("DELETE FROM kernel.trigger_exemptions WHERE proc = 'kg.count_change()'::regprocedure")
    op.execute("DROP FUNCTION kg.count_change()")
    op.execute("DROP FUNCTION kg.count_keys(kg.records[], bigint)")
    op.execute("DROP TRIGGER kg_counts_restore ON kg.counts")
    op.execute("DROP FUNCTION kg.count_restored()")
    names = ", ".join(f"'{n}'" for n in WRITERS)
    op.execute(f"""
DO $$
DECLARE
  mine oid[];
  f oid;
BEGIN
  SELECT coalesce(array_agg(oid), '{{}}') INTO mine FROM pg_proc WHERE pronamespace = 'kg'::regnamespace AND proname IN ({names});
  DELETE FROM kernel.vetted_write_pins WHERE proc::oid = ANY(mine);
  DELETE FROM kernel.function_registry WHERE proc::oid = ANY(mine);
  FOREACH f IN ARRAY mine LOOP
    EXECUTE format('DROP FUNCTION %s RESTRICT', f::regprocedure);
  END LOOP;
  DELETE FROM kernel.rls_registry WHERE rel = 'kg.counts'::regclass;
  DELETE FROM kernel.vetted_open_ops WHERE rel = 'kg.counts'::regclass;
END $$""")
    op.execute("DROP TABLE kg.counts")
    op.execute("DROP SEQUENCE kg.count_id_seq")
