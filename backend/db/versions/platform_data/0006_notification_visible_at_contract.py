"""platform data: a build that does not check notification `visible_at` must not run on this schema (the contract that platform_data_0005 left to a header)"""

revision = "platform_data_0006"
down_revision = "platform_data_0005"
branch_labels = None
depends_on = None
# A contract. A build before platform_data_0005 does not check `visible_at` in its list, count, mark-all-read and by-id paths, so it would show a
# future-dated row early and act on it. The epoch is recorded when a revision is applied, so a database that already applied 0005 never reads a change
# made to that file; this revision raises the floor for it and for a new install alike. With min_code_epoch = 5 such a build refuses to boot on this
# schema (boot_assert) and its migrate Job refuses to run, so a restarted or rolled-back pod cannot serve one. It does not stop a pod of the older
# build that is already running while the new release rolls out. After the upgrade a rollback of the code is refused as well (a development downgrade
# keeps min_code_epoch): roll forward, or restore a backup taken before the upgrade. MongoDB has no schema epoch, so there an older build reads a
# scheduled row as visible.
min_code_epoch = 5


def upgrade():
    pass          # no schema change: the epoch row is what this revision writes


def downgrade():
    pass          # development only; min_code_epoch is kept, so no build runs until the schema is upgraded again
