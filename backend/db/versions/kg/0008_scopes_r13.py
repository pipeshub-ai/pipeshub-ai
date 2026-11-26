"""kg: the access lists (kg.scopes), the stamp of a record of no connector (kg.records.r13) and their vetted writers"""
from alembic import op
from db.lib.run_sql import run_sql
from db.lib.vetted import run_vetted

revision = "kg_0008"
down_revision = "kg_0007"
branch_labels = None
depends_on = None
older_code_compatible = "adds a table, a nullable column nothing older reads and functions; older code never touches them"

WRITERS = ("w_scopes_set", "w_scopes_delete", "w_r13_set")


def upgrade():
    run_sql(__file__)
    run_vetted(__file__, *WRITERS)


def downgrade():
    # Exactly what this revision added, by name: the writers (pins and registry rows, then the write grants of the vetted role are recomputed for the tables the pins named, so a
    # grant another function's pin needs stays), the table and the column.
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
  SELECT coalesce(array_agg(DISTINCT rel), '{{}}') INTO rels FROM kernel.vetted_write_pins WHERE proc::oid = ANY(mine) AND rel <> 'kg.scopes'::regclass;
  DELETE FROM kernel.vetted_write_pins WHERE proc::oid = ANY(mine);
  DELETE FROM kernel.function_registry WHERE proc::oid = ANY(mine);
  FOREACH f IN ARRAY mine LOOP
    EXECUTE format('DROP FUNCTION %s RESTRICT', f::regprocedure);
  END LOOP;
  DELETE FROM kernel.rls_registry WHERE rel = 'kg.scopes'::regclass;
  DROP TABLE kg.scopes;
  FOREACH t IN ARRAY rels LOOP
    PERFORM kernel._sync_vetted_privs(t);
  END LOOP;
END $$""")
    op.execute("ALTER TABLE kg.records DROP COLUMN r13")
