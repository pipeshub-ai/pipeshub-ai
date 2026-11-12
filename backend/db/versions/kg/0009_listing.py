"""kg: the vetted writer of the listing columns (folder, hide_children, alien) of the records, and the indexes a listing reads"""
from alembic import op
from db.lib.run_sql import run_sql
from db.lib.vetted import run_vetted

revision = "kg_0009"
down_revision = "kg_0008"
branch_labels = None
depends_on = None
older_code_compatible = "adds a function and indexes only; older code does not call the function and an index changes no answer"

WRITERS = ("w_listing_set",)


def upgrade():
    run_sql(__file__)
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
    for k in ("name", "updated", "created", "size"):
        for d in ("asc", "desc"):
            op.execute(f"DROP INDEX kg.records_list_{k}_{d}")
    op.execute("DROP INDEX kg.records_list_class")
    op.execute("ALTER TABLE kg.records DROP COLUMN name_sort")
