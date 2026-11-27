"""platform: the connectors and query roles may write the agent harness's credential vault and control-plane credentials; indexing and kv never read them"""
import re
from pathlib import Path

from alembic import op
from db.lib.run_sql import run_sql

revision = "platform_0012"
down_revision = "platform_0011"
branch_labels = None
depends_on = None
older_code_compatible = (
    "replaced functions that widen two roles' writes by a few harness key prefixes and add keys to the indexing and kv refusal guard "
    "that no indexing or configuration-only code reads; earlier code reads and writes no key it did not before, and while "
    "platform.kv_access is in shadow every access is admitted anyway"
)


def _function(file: str, pattern: str) -> str:
    text = (Path(__file__).with_name(file)).read_text(encoding="utf-8")
    match = re.search(pattern, text, re.S)
    assert match is not None, f"{file} no longer defines the function"
    return match.group(0)


def upgrade():
    run_sql(__file__)


def downgrade():
    """Restore platform.kv_may as platform_0011 defined it and the guard as platform_0006 did, read from those revisions' own files."""
    cursor = op.get_bind().connection.cursor()
    cursor.execute(_function("0011_kv_sandbox_alerts.sql", r"CREATE OR REPLACE FUNCTION platform\.kv_may\(p_key text, p_write boolean\).*?\$\$;"))
    guard = _function("0006_kv_prefix_access.sql", r"CREATE FUNCTION platform\.kv_admin_or_credential\(p_key text\).*?\$\$;")
    cursor.execute(guard.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
