"""retrieval: the membership arms of a point's group on the point row, kept by triggers, so the vector-index tier filters without a probe"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "retrieval_0006"
down_revision = "retrieval_0005"
branch_labels = None
depends_on = None
older_code_compatible = ("adds a nullable column and triggers that keep it; older writers name the same tables and the triggers fill the "
                         "column for them, older searches never read it, and a NULL column is searched through the membership rows as before")


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS points_member_keys ON retrieval.points")
    for t in ("insert", "delete", "update"):
        op.execute(f"DROP TRIGGER IF EXISTS group_members_keys_{t} ON retrieval.group_members")
    op.execute("DELETE FROM kernel.trigger_exemptions WHERE proc::oid IN (SELECT oid FROM pg_proc WHERE proname IN ('points_member_keys', 'group_members_keys'))")
    op.execute("DROP FUNCTION IF EXISTS retrieval.points_member_keys(), retrieval.group_members_keys()")
    op.execute("ALTER TABLE retrieval.points DROP COLUMN IF EXISTS member_keys")
    op.execute("DELETE FROM kernel.lock_keys WHERE class_id = 7366")
