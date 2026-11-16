"""platform: key-value access for the provider spend guard keys (indexing reads the limits; indexing and kv create and read the install secret)"""
import re
from pathlib import Path

from alembic import op
from db.lib.run_sql import run_sql

revision = "platform_0007"
down_revision = "platform_0006"
branch_labels = None
depends_on = None
older_code_compatible = ("only the function behind the policies changes, and only to admit two more keys to two roles; "
                         "in shadow mode, the default, everything was admitted already, and earlier code never reads these keys through a refused path")


def upgrade():
    run_sql(__file__)


def downgrade():
    # platform_0006's definition, taken from its own file so the two never drift
    sql = (Path(__file__).with_name("0006_kv_prefix_access.sql")).read_text(encoding="utf-8")
    fn = re.search(r"CREATE FUNCTION platform\.kv_may.*?\$\$;", sql, re.S).group(0)
    op.get_bind().connection.cursor().execute(fn.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
