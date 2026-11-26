"""harness: the control plane's stores: org and deployment heads, policy layers, catalog, installations,
grants, bindings and the migration ledgers"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "harness_0006"
down_revision = "harness_0005"
branch_labels = None
depends_on = None


def upgrade():
    run_sql(__file__)


TABLES = (
    "cp_org_heads",
    "cp_deployment_head",
    "cp_policy_layers",
    "cp_policy_history",
    "cp_migration_ledger",
    "cp_catalog_items",
    "cp_installations",
    "cp_installation_history",
    "cp_grants",
    "cp_capability_ledger",
    "cp_bindings",
)


def downgrade():
    rels = ", ".join(f"'harness.{t}'::regclass" for t in TABLES)
    op.execute(f"DELETE FROM kernel.rls_registry WHERE rel IN ({rels})")
    op.execute("DROP TABLE " + ", ".join(f"harness.{t}" for t in TABLES))
