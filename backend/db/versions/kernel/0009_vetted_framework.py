"""kernel: vetted function registry columns, the checklist checks (kernel.vetted_violations) and the extension point's grant"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "kernel_0009"
down_revision = "kernel_0008"
branch_labels = None
depends_on = None
older_code_compatible = "the new CHECK and the replaced kernel.register_function (old three-argument calls still resolve, the new arguments default) concern the function registry, which only revisions write and no service role touches"


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("REVOKE SELECT ON platform.deployment_profile FROM pipeshub_jobs")
    op.execute("REVOKE EXECUTE ON FUNCTION kernel.assert_profile_epoch(int) FROM pipeshub_jobs")
    op.execute("REVOKE USAGE ON SCHEMA kernel, platform FROM pipeshub_jobs")
    op.execute("DROP VIEW kernel.rls_violations")
    op.execute("ALTER VIEW kernel.rls_violations_core RENAME TO rls_violations")
    op.execute("DROP VIEW kernel.vetted_violations")
    op.execute("DROP FUNCTION kernel.grant_vetted_owner_select(regclass), kernel.declare_extension_schema(name), kernel.register_function(regprocedure, text, name, text, boolean)")
    op.execute("DROP TABLE kernel.vetted_extension_reads, kernel.extension_schemas")
    op.execute("CREATE FUNCTION kernel.register_function(p_proc regprocedure, p_class text, p_writer name DEFAULT NULL) RETURNS void "
               "LANGUAGE sql SET search_path = pg_catalog, kernel, pg_temp AS $$ INSERT INTO kernel.function_registry (proc, class, writer) "
               "VALUES (p_proc, p_class, p_writer) ON CONFLICT (proc) DO UPDATE SET class = excluded.class, writer = excluded.writer $$")
    op.execute("ALTER TABLE kernel.function_registry DROP COLUMN scope, DROP COLUMN content_ok")
