"""kg: the requests to refresh the statistics of the kg tables after a connector's first load or a restore (kg.analyze_requests) and their vetted writer"""
from alembic import op
from db.lib.run_sql import run_sql
from db.lib.vetted import run_vetted

revision = "kg_0016"
down_revision = "kg_0015"
branch_labels = None
depends_on = None
older_code_compatible = "adds a table and its vetted writer; older code never reads or writes the table, and no older worker closes its requests"

WRITERS = ("w_analyze_request",)


def upgrade():
    run_sql(__file__)
    run_vetted(__file__, *WRITERS)


def downgrade():
    # Exactly what this revision added, by name: the writer (its pin and registry row), then the table.
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
  DELETE FROM kernel.rls_registry WHERE rel = 'kg.analyze_requests'::regclass;
  DELETE FROM kernel.vetted_open_ops WHERE rel = 'kg.analyze_requests'::regclass;
END $$""")
    op.execute("DROP TABLE kg.analyze_requests")
