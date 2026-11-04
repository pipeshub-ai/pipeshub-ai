"""kernel: the import role (the one-off copy of an existing install's data; policy import_all on the tables the copy writes)"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "kernel_0012"
down_revision = "kernel_0011"
branch_labels = None
depends_on = None
older_code_compatible = "kernel.apply_rls_partition keeps its signature and result and only adds the import policy to a new leaf when its root has one; a root without it behaves as before"


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("""DO $$ DECLARE r record; p regclass; BEGIN
  FOR r IN SELECT rel FROM kernel.rls_registry WHERE import_access LOOP
    FOR p IN SELECT r.rel UNION SELECT relid FROM pg_partition_tree(r.rel) LOOP
      EXECUTE format('DROP POLICY IF EXISTS import_all ON %s', p);
    END LOOP;
  END LOOP;
END $$""")
    op.execute("""DO $$ DECLARE core text; wrapper text; BEGIN
  wrapper := pg_get_viewdef('kernel.rls_violations'::regclass);
  core := pg_get_viewdef('kernel.rls_violations_core'::regclass);
  core := replace(replace(replace(replace(core,
    E',\\n            settings.importer', ''),
    E',\\n            g.import_access', ''),
    E'                    WHEN ''import_all''::text THEN s.importer\\n', ''),
    ' OR ((t.name = ''import_all''::text) AND reg.import_access)', '');
  core := replace(core, E' AND (x.role IS DISTINCT FROM (to_regrole((s.importer)::text))::oid)', '');
  DROP VIEW kernel.rls_violations;
  DROP VIEW kernel.rls_violations_core;
  EXECUTE 'CREATE VIEW kernel.rls_violations_core WITH (security_invoker = true) AS ' || core;
  EXECUTE 'CREATE VIEW kernel.rls_violations WITH (security_invoker = true) AS ' || wrapper;
  REVOKE ALL ON kernel.rls_violations_core, kernel.rls_violations FROM PUBLIC;
END $$""")
    op.execute("DELETE FROM kernel.policy_template WHERE name = 'import_all'")
    op.execute("DROP FUNCTION kernel.grant_import_access(regclass), kernel._import_policy(regclass)")
    op.execute("""CREATE OR REPLACE FUNCTION kernel.apply_rls_partition(p_child regclass, p_root regclass, p_lock_timeout text DEFAULT '2s')
RETURNS void LANGUAGE plpgsql SET search_path = pg_catalog, kernel, pg_temp AS $fn$
DECLARE g kernel.rls_registry;
BEGIN
  SELECT * INTO g FROM kernel.rls_registry WHERE rel = p_root;
  IF g IS NULL OR g.class = 'global' THEN RAISE EXCEPTION 'apply_rls_partition: % is not a registered org-scoped root', p_root; END IF;
  PERFORM set_config('lock_timeout', p_lock_timeout, true);
  PERFORM kernel._secure_rel(p_child, p_root, g.class, g.jobs_access, g.vetted_read, g.vetted_write);
END $fn$""")
    op.execute("ALTER TABLE kernel.rls_registry DROP COLUMN import_access")
    op.execute("ALTER TABLE kernel.settings DROP COLUMN importer")
