"""kg: the grants writer takes a role of any length, as the graph stores it (one long role refused a whole batch, and with it the copy of every connector the drain held)"""
from alembic import op
from db.lib.vetted import run_vetted

revision = "kg_0012"
down_revision = "kg_0011"
branch_labels = None
depends_on = None
older_code_compatible = "replaces the body of the grants writer with one that refuses less; older code calls it with the same arguments"

# The 64-character bound on p_roles bounded nothing that needs it: role is not part of any key or index of kg.scope_members. The type keeps its bound: it is part of the primary key.
SIG = "kg.w_grants_set(bigint[], bigint[], text[], text[], bigint[], boolean[])"


def upgrade():
    # The generated file creates the function, its registry row and its pin: the old ones go first, in this transaction, so the vetted role's grants never lack the pin.
    op.execute(f"DELETE FROM kernel.vetted_write_pins WHERE proc = '{SIG}'::regprocedure")
    op.execute(f"DELETE FROM kernel.function_registry WHERE proc = '{SIG}'::regprocedure")
    op.execute(f"DROP FUNCTION {SIG}")
    run_vetted(__file__, "w_grants_set")


def downgrade():
    pass      # kg_0002 owns the function and runs the same generated file: a database at kg_0011 built by this code has this body too, and older code calls it alike
