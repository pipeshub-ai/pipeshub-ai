"""kg: the vetted writer of the access stamps of kg.records"""
from alembic import op
from db.lib.vetted import run_vetted

revision = "kg_0005"
down_revision = "kg_0004"
branch_labels = None
depends_on = None
older_code_compatible = "adds a function only; older code does not call it"

WRITERS = ("w_stamps_set",)


def upgrade():
    run_vetted(__file__, *WRITERS)


def downgrade():
    # Exactly what this revision added, by name: the writers, their pins and registry rows. The write grants of the vetted role are then recomputed, for the tables those pins
    # named, from the pins that remain (kernel._sync_vetted_privs), so a grant another function's pin needs stays and the rest goes. A database stopped at kg_0004 must pass the
    # catalog check; nothing else of kg or of any other schema is touched, and the functions are dropped without cascade.
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
