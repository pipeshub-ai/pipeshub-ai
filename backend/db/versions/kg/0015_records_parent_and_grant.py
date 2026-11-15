"""kg: the records writer takes the PARENT_AND_GRANT access rule (a record with it refused its whole batch, and with it the copy of its connector)"""
from alembic import op
from db.lib.vetted import run_vetted

revision = "kg_0015"
down_revision = "kg_0014"
branch_labels = None
depends_on = None
older_code_compatible = "replaces the body of the records writer with one that refuses less; older code calls it with the same arguments"

SIG = ("kg.w_records_upsert(bigint[], smallint[], text[], smallint[], bigint[], bigint[], bigint[], text[], text[], text[], text[], text[], bigint[], bigint[], bigint[], text[], text[], "
       "text[], boolean[], boolean[], boolean[])")


def upgrade():
    # The generated file creates the function, its registry row and its pin: the old ones go first, in this transaction, so the vetted role's grants never lack the pin.
    op.execute(f"DELETE FROM kernel.vetted_write_pins WHERE proc = '{SIG}'::regprocedure")
    op.execute(f"DELETE FROM kernel.function_registry WHERE proc = '{SIG}'::regprocedure")
    op.execute(f"DROP FUNCTION {SIG}")
    run_vetted(__file__, "w_records_upsert")


def downgrade():
    pass      # kg_0002 owns the function and runs the same generated file: a database at kg_0014 built by this code has this body too, and older code calls it alike
