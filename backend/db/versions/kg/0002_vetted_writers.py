"""kg: the vetted writers: the only code that writes the kg tables"""
from alembic import op
from db.lib.vetted import run_vetted

revision = "kg_0002"
down_revision = "kg_0001"
branch_labels = None
depends_on = None
older_code_compatible = "adds functions only; older code does not call them"

WRITERS = ("w_nodes_upsert", "w_records_upsert", "w_records_delete", "w_links_set", "w_links_delete", "w_grants_set", "w_grants_delete",
           "w_closure_set", "w_closure_delete", "w_changes_ack", "w_changes_delete", "w_projection_set", "w_classes_set",
           "w_class_literals_set", "w_app_facts_set", "w_app_cross_set")


def upgrade():
    run_vetted(__file__, *WRITERS)


def downgrade():
    # Exactly what this revision added, by name: the writers, their pins and registry rows, and the write grants those pins gave the vetted role on a table
    # no other function is still pinned to. A database stopped at kg_0001 must pass the catalog check; nothing else of kg or of any other schema is touched.
    names = ", ".join(f"'{n}'" for n in WRITERS)
    op.execute(f"""
DO $$
DECLARE
  mine oid[];
  t regclass;
  f oid;
BEGIN
  SELECT coalesce(array_agg(oid), '{{}}') INTO mine FROM pg_proc WHERE pronamespace = 'kg'::regnamespace AND proname IN ({names});
  FOR t IN SELECT DISTINCT p.rel FROM kernel.vetted_write_pins p WHERE p.proc::oid = ANY(mine)
           AND NOT EXISTS (SELECT 1 FROM kernel.vetted_write_pins o WHERE o.rel = p.rel AND NOT (o.proc::oid = ANY(mine)))
  LOOP
    EXECUTE format('REVOKE INSERT, UPDATE, DELETE ON %s FROM pipeshub_vetted_kg', t);
  END LOOP;
  DELETE FROM kernel.vetted_write_pins WHERE proc::oid = ANY(mine);
  DELETE FROM kernel.function_registry WHERE proc::oid = ANY(mine);
  FOREACH f IN ARRAY mine LOOP
    EXECUTE format('DROP FUNCTION %s', f::regprocedure);
  END LOOP;
END $$""")
