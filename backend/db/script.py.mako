"""${message}"""
from db.lib.run_sql import run_sql

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade():
    run_sql(__file__)


def downgrade():
    pass
