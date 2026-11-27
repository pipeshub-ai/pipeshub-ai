"""platform data: a notification may carry a key its recipient holds once (`dedupe_key`), so an event delivered again stores nothing; no build from before scheduled notifications runs on the schema"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index
from db.lib.run_sql import run_sql

revision = "platform_data_0011"
down_revision = "platform_data_0010"
branch_labels = None
depends_on = None
# A contract. A build before platform_data_0005 does not check `visible_at` in its list, count, mark-all-read and by-id paths, so it would
# show a future-dated row early and act on it: with min_code_epoch = 5 such a build refuses to boot on this schema (boot_assert) and its
# migrate Job refuses to run. It does not stop a pod of the older build that is already running while the new release rolls out, and after
# the upgrade a rollback of the code to such a build is refused as well: roll forward, or restore a backup taken before the upgrade. A build
# of epoch 5 still runs: it never names `dedupe_key`, so its inserts leave it NULL, and an event delivered again on its pod stores a second
# notification for the same recipient, as before this revision. MongoDB has no schema epoch, so there an older build reads a scheduled row
# as visible.
min_code_epoch = 5
blocking_index_ok = "the index keys dedupe_key, which this revision adds, so it cannot be built before its transaction"


def upgrade():
    run_sql(__file__)
    # the duplicate check: one recipient's rows by key. Built inside the upgrade's one transaction, so writers of the table wait for it.
    ensure_partitioned_index(op.get_bind(), "platform.notifications", "notifications_dedupe", "org_id, assigned_to, dedupe_key", where="dedupe_key IS NOT NULL", blocking=True)


def downgrade():
    op.execute("DROP INDEX platform.notifications_dedupe")
    op.execute("ALTER TABLE platform.notifications DROP COLUMN dedupe_key")
