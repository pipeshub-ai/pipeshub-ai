"""harness: runs, their v1 events, session counters, command and admission receipts, admission quotas, next runs,
liveness records and pending sandbox reaps"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "harness_0003"
down_revision = "harness_0002"
branch_labels = None
depends_on = None

_TABLES = (
    "harness.runs",
    "harness.run_events",
    "harness.session_counters",
    "harness.command_receipts",
    "harness.admission_receipts",
    "harness.admission_quotas",
    "harness.next_runs",
    "harness.liveness",
    "harness.sandbox_reaps",
)


def upgrade():
    run_sql(__file__)


def downgrade():
    rels = ", ".join(f"'{t}'::regclass" for t in _TABLES)
    op.execute(f"DELETE FROM kernel.rls_registry WHERE rel IN ({rels})")
    op.execute("DROP TABLE " + ", ".join(_TABLES))
