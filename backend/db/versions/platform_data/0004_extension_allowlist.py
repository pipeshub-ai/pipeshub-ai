"""platform data: pg_search and pg_stat_statements may be installed (the kernel's allow-list of extensions)"""
from db.lib.run_sql import run_sql

revision = "platform_data_0004"
down_revision = "platform_data_0003"
branch_labels = None
depends_on = None
older_code_compatible = "adds two names to the list of extensions the catalog check accepts; no table, column, privilege or function changes"


def upgrade():
    run_sql(__file__)


def downgrade():
    pass          # both names are also in kernel_0003, which this file only repeats: the list keeps them
