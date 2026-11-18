"""platform data: a notification may be scheduled (`visible_at`), and is not read before that instant"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index
from db.lib.run_sql import run_sql

revision = "platform_data_0005"
down_revision = "platform_data_0004"
branch_labels = None
depends_on = None
# A contract. A build before this revision does not check `visible_at` in its list, count, mark-all-read and by-id paths, so it would show a
# future-dated row early and act on it. With min_code_epoch = 5 such a build refuses to boot on this schema (boot_assert) and its migrate Job
# refuses to run, so a restarted or rolled-back pod cannot serve one. It does not stop a pod of the older build that is already running while the
# new release rolls out. After the upgrade a rollback of the code is refused as well (a development downgrade keeps min_code_epoch): roll
# forward, or restore a backup taken before the upgrade. MongoDB has no schema epoch, so there an older build reads a scheduled row as visible.
min_code_epoch = 5


def upgrade():
    run_sql(__file__)
    # the inbox filter: one user's rows by status and visibility. Built inside the upgrade's one transaction, so writers of the table wait for it.
    ensure_partitioned_index(op.get_bind(), "platform.notifications", "notifications_visible", "org_id, assigned_to, status, visible_at", where="NOT is_deleted", blocking=True)


def downgrade():
    # Development only (it keeps min_code_epoch, so no build runs until the schema is upgraded again). Dropping the column forgets every
    # `visible_at`: a row still scheduled stops being scheduled and, on a later upgrade, is visible at once.
    op.execute("DROP INDEX platform.notifications_visible")
    op.execute("ALTER TABLE platform.notifications DROP COLUMN visible_at")
