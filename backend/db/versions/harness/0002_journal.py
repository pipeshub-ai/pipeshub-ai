"""harness: the session journal, its stream counters and payloads, and the deployment-wide leases"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "harness_0002"
down_revision = "harness_0001"
branch_labels = None
depends_on = None


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DELETE FROM kernel.rls_registry WHERE rel IN ('harness.session_events'::regclass, "
               "'harness.stream_counters'::regclass, 'harness.journal_payloads'::regclass, 'harness.leases'::regclass)")
    op.execute("DROP TABLE harness.session_events, harness.stream_counters, harness.journal_payloads, harness.leases")
