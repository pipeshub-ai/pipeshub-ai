"""kg: the records writer takes a name of any length, as the graph stores it (one long name refused a whole batch, and with it the copy of its connector)"""
from alembic import op
from db.lib.vetted import run_vetted

revision = "kg_0010"
down_revision = "kg_0009"
branch_labels = None
depends_on = None
older_code_compatible = "replaces the body of the records writer with one that refuses less; older code calls it with the same arguments"

# The 4,000-character bound on p_names and p_name_keys bounded nothing that needs it: no index keys on name or name_key, and the name indexes of kg_0009 key on a prefix of name_sort.
SIG = ("kg.w_records_upsert(bigint[], smallint[], text[], smallint[], bigint[], bigint[], bigint[], text[], text[], text[], text[], text[], bigint[], bigint[], bigint[], text[], text[], "
       "text[], boolean[], boolean[], boolean[])")


def upgrade():
    # The generated file creates the function, its registry row and its pin: the old ones go first, in this transaction, so the vetted role's grants never lack the pin.
    op.execute(f"DELETE FROM kernel.vetted_write_pins WHERE proc = '{SIG}'::regprocedure")
    op.execute(f"DELETE FROM kernel.function_registry WHERE proc = '{SIG}'::regprocedure")
    op.execute(f"DROP FUNCTION {SIG}")
    run_vetted(__file__, "w_records_upsert")


def downgrade():
    pass      # kg_0002 owns the function and runs the same generated file: a database at kg_0009 built by this code has this body too, and older code calls it alike
