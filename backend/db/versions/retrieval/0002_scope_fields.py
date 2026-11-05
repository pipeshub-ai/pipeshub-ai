"""retrieval: the permission scope fields of a group (stamp, version, generation, path and override-scope lists) and group_members kinds 4 and 5"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "retrieval_0002"
down_revision = "retrieval_0001"
branch_labels = None
depends_on = None
older_code_compatible = ("adds nullable columns and widens a check; code that does not know them never touches them (its member diff is per kind, "
                         "and the maintenance job removes only the members of groups that hold no points)")


def upgrade():
    run_sql(__file__)


def downgrade():
    # The table forces row-level security, which would hide every org's rows from the owner: lift it for the delete so that no org keeps a row the old check refuses.
    op.execute("ALTER TABLE retrieval.group_members NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM retrieval.group_members WHERE kind > 3")
    op.execute("ALTER TABLE retrieval.group_members FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE retrieval.group_members DROP CONSTRAINT group_members_kind_check")
    op.execute("ALTER TABLE retrieval.group_members ADD CONSTRAINT group_members_kind_check CHECK (kind IN (1, 2, 3))")
    op.execute("ALTER TABLE retrieval.point_groups DROP COLUMN path_ids, DROP COLUMN acl_scope, DROP COLUMN scope_stamp, "
               "DROP COLUMN scope_version, DROP COLUMN scope_generation")
