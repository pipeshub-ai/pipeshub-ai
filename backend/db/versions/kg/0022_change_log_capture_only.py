"""kg: a service role's row in the change log comes from the capture trigger (or is a demotion request); a row it inserts itself is refused"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "kg_0022"
down_revision = "kg_0021"
branch_labels = None
depends_on = None
older_code_compatible = ("replaces the body of the change log's stamp trigger; older code writes the log only through the capture triggers and the demotion request, "
                         "both of which the new body still lets through, so it runs unchanged")

# kg_0004's body, put back by the downgrade.
KG_0004_STAMP = """CREATE OR REPLACE FUNCTION kg.stamp_change() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $fn$
BEGIN
  IF current_user IN ('pipeshub_connectors', 'pipeshub_indexing', 'pipeshub_query') THEN
    NEW.id := nextval('kg.graph_changes_id_seq');
    NEW.txid := pg_current_xact_id();
  END IF;
  RETURN NEW;
END $fn$"""


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute(KG_0004_STAMP)
    op.execute("SELECT kernel.exempt_trigger('kg.stamp_change()', 'sets the identifier and the transaction id of a change-log row for the service roles; reads and writes nothing else', 'security-review')")
