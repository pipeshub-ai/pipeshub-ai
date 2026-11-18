"""retrieval: the document frequency of each term, kept by triggers on the postings, so the lexical search reads it instead of counting"""
from pathlib import Path

from alembic import op
from db.lib.run_sql import run_sql
from db.lib.vetted import run_vetted

revision = "retrieval_0005"
down_revision = "retrieval_0004"
branch_labels = None
depends_on = None
older_code_compatible = ("adds tables and triggers that keep themselves; older writers name the postings as before and the triggers record "
                         "their changes, and the search function keeps its signature")

TABLES = ("retrieval.lexical_df", "retrieval.lexical_term_delta", "retrieval.lexical_df_pending")
SIG = "retrieval.search_sparse(smallint, bigint[], real[], text[], text[], text[], text[], text[], int)"


def upgrade():
    run_sql(__file__)
    # The generated file creates the function and its registry row: the old ones go first, in this transaction.
    op.execute(f"DELETE FROM kernel.function_registry WHERE proc = '{SIG}'::regprocedure")
    op.execute(f"DROP FUNCTION {SIG}")
    run_vetted(__file__, "search_sparse")


def downgrade():
    for event in ("insert", "delete", "update"):
        op.execute(f"DROP TRIGGER IF EXISTS postings_df_{event} ON retrieval.postings")
    op.execute("DELETE FROM kernel.trigger_exemptions WHERE proc = 'retrieval.postings_df_capture()'::regprocedure")
    op.execute("DROP FUNCTION retrieval.postings_df_capture()")
    op.execute(f"DELETE FROM kernel.function_registry WHERE proc = '{SIG}'::regprocedure")
    op.execute(f"DROP FUNCTION {SIG}")
    op.execute(f"DELETE FROM kernel.rls_registry WHERE rel::text IN ({', '.join(repr(t) for t in TABLES)})")
    op.execute(f"DROP TABLE {', '.join(TABLES)}")
    # the function as the previous release had it: the generated file now reads the tables this downgrade drops
    op.get_bind().connection.cursor().execute((Path(__file__).parent / "previous/search_sparse_retrieval_0004.sql").read_text(encoding="utf-8"))
