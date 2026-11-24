"""retrieval: a bound on each term's weights beside its frequency, so the lexical search sums only the postings that can change its answer"""
import re
from pathlib import Path

from alembic import op
from db.lib.run_sql import run_sql
from db.lib.vetted import run_vetted

revision = "retrieval_0008"
down_revision = "retrieval_0007"
branch_labels = None
depends_on = None
older_code_compatible = ("adds nullable columns, replaces the body of the postings trigger function and of the lexical search with the same "
                         "signatures and answers; older writers fire the same triggers, older fold and recount leave the new columns NULL, "
                         "which the search reads as not known and answers by the exhaustive sum")

SIG = "retrieval.search_sparse(smallint, bigint[], real[], text[], text[], text[], text[], text[], int)"


def upgrade():
    run_sql(__file__)
    # The generated file creates the function and its registry row: the old ones go first, in this transaction.
    op.execute(f"DELETE FROM kernel.function_registry WHERE proc = '{SIG}'::regprocedure")
    op.execute(f"DROP FUNCTION {SIG}")
    run_vetted(__file__, "search_sparse")


def downgrade():
    here = Path(__file__).parent
    op.execute(f"DELETE FROM kernel.function_registry WHERE proc = '{SIG}'::regprocedure")
    op.execute(f"DROP FUNCTION {SIG}")
    cur = op.get_bind().connection.cursor()
    cur.execute((here / "previous/search_sparse_retrieval_0007.sql").read_text(encoding="utf-8"))
    # retrieval_0005's trigger body and its exemption (which holds the body's hash), from its own file
    old = (here / "0005_term_frequencies.sql").read_text(encoding="utf-8")
    body = re.findall(r"^CREATE FUNCTION retrieval\.postings_df_capture\(\).*?^END \$fn\$;", old, re.S | re.M)
    exemption = re.findall(r"^SELECT kernel\.exempt_trigger\('retrieval\.postings_df_capture\(\)'.*?'security-review'\);", old, re.S | re.M)
    assert len(body) == len(exemption) == 1, (len(body), len(exemption))
    cur.execute(body[0].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    cur.execute(exemption[0])
    op.execute("ALTER TABLE retrieval.lexical_term_delta DROP COLUMN wmax")
    op.execute("ALTER TABLE retrieval.lexical_df DROP COLUMN wmax")
