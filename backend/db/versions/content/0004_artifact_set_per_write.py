"""content: one artifact-set row per stored write of a revision, not one per revision"""
from alembic import op
from db.lib.ddl import ensure_index
from db.lib.run_sql import run_sql

revision = "content_0004"
down_revision = "content_0003"
branch_labels = None
depends_on = None
# A contract: older code keys an artifact set by revision alone, so it would take another write's row for its own and file its
# files at the revision's root, which that row's reader does not read.
min_code_epoch = 4


def upgrade():
    # Every stored write of a content revision files its artifact set in a row of its own (artifact_set->>'writeId'), so a write that
    # lost its place, or an upload storage commits late, never lands in another write's set. A row without a write id (a set from
    # before writes had their own) reads as '' and stays one per revision. Every existing row already satisfies this index (it is
    # looser than the one it replaces). Built in the upgrade's transaction: writers of the table wait for a build that reads only the
    # rows of artifact sets.
    ensure_index(
        op.get_bind(), "content.documents_artifact_set_write",
        "CREATE UNIQUE INDEX documents_artifact_set_write ON content.documents "
        "(org_id, (artifact_set->>'virtualRecordId'), (artifact_set->>'rev'), (COALESCE(artifact_set->>'writeId', ''))) "
        "WHERE artifact_set IS NOT NULL",
    )
    run_sql(__file__)


def downgrade():
    # Development only. Fails while a revision has more than one row, as the older index cannot hold them.
    op.execute(
        "CREATE UNIQUE INDEX documents_artifact_set ON content.documents "
        "(org_id, (artifact_set->>'virtualRecordId'), (artifact_set->>'rev')) WHERE artifact_set IS NOT NULL"
    )
    op.execute("DROP INDEX content.documents_artifact_set_write")
