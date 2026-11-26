"""retrieval: the membership-key triggers read a group's members through the primary key, whatever the statistics of the org's leaf say"""
import re
from pathlib import Path

from alembic import op
from db.lib.run_sql import run_sql

revision = "retrieval_0007"
down_revision = "retrieval_0006"
branch_labels = None
depends_on = None
older_code_compatible = ("replaces the bodies of two trigger functions with the same signatures and answers; older writers fire the "
                         "same triggers and get the same membership keys")


def upgrade():
    run_sql(__file__)


def downgrade():
    # retrieval_0006's bodies and their exemptions (which hold each body's hash), from its own file
    old = (Path(__file__).parent / "0006_member_keys.sql").read_text(encoding="utf-8")
    bodies = re.findall(r"^CREATE FUNCTION retrieval\.(?:points_member_keys|group_members_keys)\(\).*?^END \$fn\$;", old, re.S | re.M)
    exemptions = re.findall(r"^SELECT kernel\.exempt_trigger\(.*?'security-review'\);", old, re.S | re.M)
    assert len(bodies) == len(exemptions) == 2, (len(bodies), len(exemptions))
    cur = op.get_bind().connection.cursor()
    for sql in [b.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1) for b in bodies] + exemptions:
        cur.execute(sql)
