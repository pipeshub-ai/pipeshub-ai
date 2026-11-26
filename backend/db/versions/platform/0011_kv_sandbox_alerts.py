"""platform: the sandbox alert cooldown claims in the query role's key-value write allow-list"""
import re
from pathlib import Path

from alembic import op
from db.lib.run_sql import run_sql

revision = "platform_0011"
down_revision = "platform_0010"
branch_labels = None
depends_on = None
older_code_compatible = "the matrix grants one more prefix to write and refuses none it granted, so earlier code reads and writes as before"


def upgrade():
    run_sql(__file__)


def downgrade():
    """Restore platform.kv_may as platform_0010 defined it, read from that revision's own file."""
    text = (Path(__file__).with_name("0010_connectors_epoch_keys.sql")).read_text(encoding="utf-8")
    match = re.search(r"CREATE OR REPLACE FUNCTION platform\.kv_may\(p_key text, p_write boolean\).*?\$\$;", text, re.S)
    assert match is not None, "platform_0010 no longer defines platform.kv_may"
    op.get_bind().connection.cursor().execute(match.group(0))
