"""harness: the budget ledger, the audit chain and its anchors and checks, data keys and sandbox
snapshots"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "harness_0004"
down_revision = "harness_0003"
branch_labels = None
depends_on = None

TABLES = [
    "harness.budget_accounts",
    "harness.budget_reservations",
    "harness.audit_records",
    "harness.audit_heads",
    "harness.audit_pending",
    "harness.audit_anchors",
    "harness.audit_chain_checks",
    "harness.data_keys",
    "harness.sandbox_snapshots",
]


def upgrade():
    run_sql(__file__)


def downgrade():
    regclasses = ", ".join(f"'{t}'::regclass" for t in TABLES)
    op.execute(f"DELETE FROM kernel.rls_registry WHERE rel IN ({regclasses})")
    op.execute(f"DROP TABLE {', '.join(TABLES)}")
