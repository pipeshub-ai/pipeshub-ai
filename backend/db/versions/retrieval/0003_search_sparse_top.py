"""retrieval: the lexical search sorts the sums of the postings straight into its top-N, without storing them all first (the same rows, less work)"""
from alembic import op
from db.lib.vetted import run_vetted

revision = "retrieval_0003"
down_revision = "retrieval_0002"
branch_labels = None
depends_on = None
older_code_compatible = "replaces the body of the lexical search with one that returns the same rows for the same arguments; older code calls it alike"

SIG = "retrieval.search_sparse(smallint, bigint[], real[], text[], text[], text[], text[], text[], int)"


def upgrade():
    # The generated file creates the function and its registry row: the old ones go first, in this transaction.
    op.execute(f"DELETE FROM kernel.function_registry WHERE proc = '{SIG}'::regprocedure")
    op.execute(f"DROP FUNCTION {SIG}")
    run_vetted(__file__, "search_sparse")


def downgrade():
    pass      # retrieval_0001 owns the function and runs the same generated file: a database at retrieval_0002 built by this code has this body too, and older code calls it alike
