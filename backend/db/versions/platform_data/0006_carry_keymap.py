"""platform data: docstore-migrate's key map, and the harness tables the copy now carries (R45-CARRY)"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "platform_data_0006"
down_revision = "platform_data_0005"
branch_labels = None
depends_on = ("harness_0011",)
older_code_compatible = "adds a table only the import role reads and writes, and the import role's policy and privileges on harness tables; no application role changes and no existing column or function changes"

CARRIED = (
    "harness.personal_choices", "harness.data_keys",
    "harness.workspace_heads", "harness.workspace_manifests", "harness.workspace_keys",
    "harness.audit_records", "harness.audit_heads", "harness.audit_pending", "harness.audit_anchors",
)


def upgrade():
    run_sql(__file__)


def downgrade():
    for t in CARRIED:
        op.execute(f"DROP POLICY IF EXISTS import_all ON {t}")
        op.execute(f"UPDATE kernel.rls_registry SET import_access = false WHERE rel = '{t}'::regclass")
        op.execute(f"REVOKE ALL ON {t} FROM pipeshub_import")
    op.execute("REVOKE USAGE ON SCHEMA harness FROM pipeshub_import")
    op.execute("DROP TABLE platform.docstore_keymap")
    op.execute("DELETE FROM kernel.rls_registry WHERE rel::oid NOT IN (SELECT oid FROM pg_class)")
