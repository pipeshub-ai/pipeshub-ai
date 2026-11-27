"""kernel: an extension schema may have its own fresh vetted owner (the one the role bootstrap creates from the location's manifest)"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "kernel_0023"
down_revision = "kernel_0022"
branch_labels = None
depends_on = None
older_code_compatible = "declare_extension_schema accepts one more case (a fresh vetted owner); every schema it accepted before it still accepts"

# kernel_0009's body
_DECLARE_BEFORE = r"""CREATE OR REPLACE FUNCTION kernel.declare_extension_schema(p_schema name) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, kernel, pg_temp AS $fn$
DECLARE s kernel.settings;
BEGIN
  SELECT * INTO s FROM kernel.settings;
  IF p_schema IN ('kernel', 'platform', 'ext', 'public', 'information_schema') OR p_schema::text LIKE 'pg\_%' THEN
    RAISE EXCEPTION 'declare_extension_schema: % is not available to an extension', p_schema;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = p_schema) THEN RAISE EXCEPTION 'declare_extension_schema: no schema %', p_schema; END IF;
  IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = p_schema)
     OR EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = p_schema)
     OR EXISTS (SELECT 1 FROM pg_roles WHERE rolname = s.vetted_prefix || p_schema) THEN
    RAISE EXCEPTION 'declare_extension_schema: % is not new (declare it before creating anything in it)', p_schema;
  END IF;
  INSERT INTO kernel.extension_schemas VALUES (p_schema) ON CONFLICT DO NOTHING;
END $fn$;"""


def upgrade() -> None:
    run_sql(__file__)


def downgrade() -> None:
    op.execute(_DECLARE_BEFORE)
    op.execute("DROP FUNCTION kernel._fresh_vetted_owner(oid)")
