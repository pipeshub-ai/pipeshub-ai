"""kernel: org isolation registry, policy templates, apply_rls helpers, exemptions, function registry, restore window"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "kernel_0003"
down_revision = "kernel_0002"
branch_labels = None
depends_on = None
older_code_compatible = "creates the isolation registry and helpers before any runtime code exists; the dynamic statements create roles and revoke PUBLIC EXECUTE on the pg_catalog large-object functions, which no application code calls"


def upgrade():
    run_sql(__file__)


def downgrade():
    # Development only. Policies on registered tables go with restore_allows (CASCADE).
    op.execute("DROP FUNCTION kernel.restore_allows(text) CASCADE")
    op.execute("DROP FUNCTION kernel.apply_rls_partition(regclass, regclass, text), "
               "kernel.apply_rls(regclass, text, boolean, boolean, text, boolean), "
               "kernel._secure_rel(regclass, regclass, text, boolean, boolean, boolean), "
               "kernel.register_function(regprocedure, text, name), kernel.register_internal(regclass), kernel.exempt_constraint(regclass, name, text, text, text)")
    op.execute("DROP TABLE kernel.restore_window, kernel.function_registry, kernel.rls_exemptions, "
               "kernel.policy_template, kernel.rls_registry, kernel.settings")
