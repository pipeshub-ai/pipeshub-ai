"""platform: the PDF page limit audit's keys in the indexing role's key-value allow-lists"""
import re
from pathlib import Path

from alembic import op
from db.lib.run_sql import run_sql

revision = "platform_0012"
down_revision = "platform_0011"
branch_labels = None
depends_on = None
older_code_compatible = "the matrix grants one more prefix to read and write and refuses none it granted, so earlier code reads and writes as before"


def upgrade():
    run_sql(__file__)


def downgrade():
    """Restore platform.kv_may as platform_0011 defined it, read from that revision's own file."""
    text = (Path(__file__).with_name("0011_kv_sandbox_alerts.sql")).read_text(encoding="utf-8")
    match = re.search(r"CREATE OR REPLACE FUNCTION platform\.kv_may\(p_key text, p_write boolean\).*?\$\$;", text, re.S)
    assert match is not None, "platform_0011 no longer defines platform.kv_may"
    op.get_bind().connection.cursor().execute(match.group(0))
