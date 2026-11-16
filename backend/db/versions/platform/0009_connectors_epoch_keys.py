"""platform: the connectors role may write the embedding epoch keys (the search-index cleanup moves the epoch before it resets)"""
import re
from pathlib import Path

from alembic import op
from db.lib.run_sql import run_sql

revision = "platform_0009"
down_revision = "platform_0008"
branch_labels = None
depends_on = None
older_code_compatible = (
    "a replaced function that only widens one role's writes by one key prefix; earlier code writes no key it did not before, "
    "and while platform.kv_access is in shadow every access is admitted anyway"
)


def upgrade():
    run_sql(__file__)


def downgrade():
    """Restore platform.kv_may as platform_0007 defined it, read from that revision's own file."""
    text = (Path(__file__).with_name("0007_kv_spend_guard.sql")).read_text(encoding="utf-8")
    match = re.search(r"CREATE OR REPLACE FUNCTION platform\.kv_may\(p_key text, p_write boolean\).*?\$\$;", text, re.S)
    assert match is not None, "platform_0007 no longer defines platform.kv_may"
    op.get_bind().connection.cursor().execute(match.group(0))
