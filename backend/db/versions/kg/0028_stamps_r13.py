"""kg: one vetted writer for a record's stamps and its r13 (kg.w_stamps_r13_set), so that the stamper rewrites a row once instead of twice; w_stamps_set and w_r13_set stay for
code of the previous release"""
from pathlib import Path

from alembic import op
from db.lib.vetted import load, run_vetted

revision = "kg_0028"
down_revision = "kg_0027"
branch_labels = None
depends_on = None

WRITERS = ("w_stamps_r13_set",)
_DIR = Path(__file__).parent


def upgrade():
    run_vetted(__file__, *WRITERS)


def downgrade():
    # The function, its registry row and its pin go together, in this transaction, so the vetted role's grants never lack a pin (as kg_0027 does).
    for fn in (f for f in load(_DIR / "vetted" / "manifest.yaml") if f.name in WRITERS):
        op.execute(f"DELETE FROM kernel.vetted_write_pins WHERE proc = to_regprocedure('{fn.signature}')")
        op.execute(f"DELETE FROM kernel.function_registry WHERE proc = to_regprocedure('{fn.signature}')")
        op.execute(f"DROP FUNCTION IF EXISTS {fn.signature}")
    op.execute("SELECT kernel._sync_vetted_privs('kg.records'::regclass)")
