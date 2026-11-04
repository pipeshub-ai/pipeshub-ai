"""kg: the vetted writers: the only code that writes the kg tables"""
import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from db.lib.vetted import load, run_vetted

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
    # Exactly what this revision added, by exact signature (an overload that shares a writer's name is not a writer): the writers, their pins and registry rows. The write
    # grants of the vetted role for the tables those pins named are then recomputed from the pins that remain (kernel._sync_vetted_privs), so a grant another function's pin
    # needs stays and the rest goes. A database stopped at kg_0001 must pass the catalog check; nothing else of kg or of any other schema is touched, and the functions are
    # dropped without cascade. The signatures reach the statement as a bound parameter.
    sigs = [f.signature for f in load(Path(__file__).parent / "vetted" / "manifest.yaml") if f.qualified in {"kg." + n for n in WRITERS}]
    assert len(sigs) == len(WRITERS), "the manifest names every writer of this revision"
    op.execute(sa.text("SELECT set_config('kg_0002.writers', :sigs, true)").bindparams(sigs=json.dumps(sigs)))
    op.execute("""
DO $$
DECLARE
  mine oid[];
  rels regclass[];
  t regclass;
  f oid;
BEGIN
  SELECT coalesce(array_agg(to_regprocedure(s)::oid), '{}') INTO mine FROM jsonb_array_elements_text(current_setting('kg_0002.writers')::jsonb) s WHERE to_regprocedure(s) IS NOT NULL;
  SELECT coalesce(array_agg(DISTINCT rel), '{}') INTO rels FROM kernel.vetted_write_pins WHERE proc::oid = ANY(mine);
  DELETE FROM kernel.vetted_write_pins WHERE proc::oid = ANY(mine);
  DELETE FROM kernel.function_registry WHERE proc::oid = ANY(mine);
  FOREACH t IN ARRAY rels LOOP
    PERFORM kernel._sync_vetted_privs(t);
  END LOOP;
  FOREACH f IN ARRAY mine LOOP
    EXECUTE format('DROP FUNCTION %s RESTRICT', f::regprocedure);
  END LOOP;
END $$""")
