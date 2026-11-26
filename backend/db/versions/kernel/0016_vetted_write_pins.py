"""kernel: pinned vetted writes (DELETE and ON CONFLICT for named functions on named tables, in fixed shapes) and the checks that hold them to it"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "kernel_0016"
down_revision = "kernel_0015"
branch_labels = None
depends_on = None
older_code_compatible = ("a new registry table, a new owner-only function and a new check view; kernel.rls_violations keeps its columns and every earlier check "
                         "and only stops reporting rules 57, 88 and 89 for a function or table that carries a pin, and no earlier code has one")


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("""DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM kernel.vetted_write_pins) THEN
    RAISE EXCEPTION 'kernel_0016 downgrade: vetted write pins exist; remove the pinned functions (their module downgrades first) before the kernel goes back';
  END IF;
END $$""")
    op.execute("DROP VIEW kernel.rls_violations")
    op.execute("""CREATE VIEW kernel.rls_violations WITH (security_invoker = true) AS
SELECT c.rule, c.object FROM kernel.rls_violations_core c
  WHERE NOT (c.rule = '42 vetted role pipeshub_vetted_kernel holds SELECT outside its schema' AND c.object IN (SELECT rel::text FROM kernel.vetted_extension_reads))
    AND NOT EXISTS (SELECT 1 FROM kernel.job_gate_grants j WHERE c.rule = '42 vetted role ' || j.owner || ' holds SELECT outside its schema' AND c.object = j.rel)
UNION ALL
SELECT v.rule, v.object FROM kernel.vetted_violations v
  WHERE NOT EXISTS (SELECT 1 FROM kernel.job_gate_grants j WHERE v.rule = '73 vetted role ' || j.owner || ' holds SELECT on a table that did not opt in' AND v.object = j.rel)
    AND NOT (v.rule = '57 write function uses ON CONFLICT' AND v.object IN (SELECT proc::text FROM kernel.function_registry WHERE dedupe_rel IS NOT NULL))
UNION ALL
SELECT pv.rule, pv.object FROM kernel.person_violations pv
  WHERE NOT (pv.rule = '57 write function uses ON CONFLICT' AND pv.object IN (SELECT proc::text FROM kernel.function_registry WHERE dedupe_rel IS NOT NULL))""")
    op.execute("REVOKE ALL ON kernel.rls_violations FROM PUBLIC")
    op.execute("DROP VIEW kernel.pin_violations")
    op.execute("DROP FUNCTION kernel.allow_vetted_write_op(regprocedure, regclass, text, name[], name[], name, int)")
    op.execute("DROP TABLE kernel.vetted_write_pins")
    op.execute("DROP TABLE kernel.pin_writers")
