"""platform data: a notification may be scheduled (`visible_at`), and is not read before that instant"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index
from db.lib.run_sql import run_sql

revision = "platform_data_0005"
down_revision = "platform_data_0004"
branch_labels = None
depends_on = None
older_code_compatible = "adds one nullable column without a default and one index; older code never names the column, so its inserts leave it NULL and its queries keep working. Older code does not know scheduling: after a rollback, or on an old pod during a rolling deploy, a row stored with a future visible_at is listed, counted and marked read as if visible now. Only its own recipient sees it, so no access changes; an early notice is accepted rather than refusing every rollback past this revision"
blocking_index_ok = "the index keys visible_at, which this revision adds, so it cannot be built before its transaction"


def upgrade():
    run_sql(__file__)
    # the inbox filter: one user's rows by status and visibility. Built inside the upgrade's one transaction, so writers of the table wait for it.
    ensure_partitioned_index(op.get_bind(), "platform.notifications", "notifications_visible", "org_id, assigned_to, status, visible_at", where="NOT is_deleted", blocking=True)


def downgrade():
    op.execute("DROP INDEX platform.notifications_visible")
    op.execute("ALTER TABLE platform.notifications DROP COLUMN visible_at")
