"""retrieval: the vetted check whether another org still holds points of a vrid (true or false only), for the delete of a virtual record"""
from alembic import op
from db.lib.vetted import run_vetted

revision = "retrieval_0004"
down_revision = "retrieval_0003"
branch_labels = None
depends_on = None
older_code_compatible = "adds a function only; older code does not call it"

FUNCTIONS = {"vrid_held_elsewhere": "text"}


def upgrade():
    run_vetted(__file__, *FUNCTIONS)


def downgrade():
    for name, args in FUNCTIONS.items():
        op.execute(f"DROP FUNCTION IF EXISTS retrieval.{name}({args})")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
