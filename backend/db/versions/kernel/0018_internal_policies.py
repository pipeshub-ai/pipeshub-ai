"""kernel: row policies on internal tables, registered and kept as registered (check 04 exemption, checks 140 and 141)"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "kernel_0018"
down_revision = "kernel_0017"
branch_labels = None
depends_on = None
older_code_compatible = ("a new owner-only registry and two owner-only functions; the check view gains an exemption that only a registered "
                         "policy uses and two checks that are empty until one is registered, so earlier code sees the same tables and grants")

_KERNEL_0017_VIEW = """CREATE VIEW kernel.rls_violations WITH (security_invoker = true) AS
SELECT c.rule, c.object FROM kernel.rls_violations_core c
  WHERE NOT (c.rule = '42 vetted role pipeshub_vetted_kernel holds SELECT outside its schema' AND c.object IN (SELECT rel::text FROM kernel.vetted_extension_reads))
    AND NOT EXISTS (SELECT 1 FROM kernel.job_gate_grants j WHERE c.rule = '42 vetted role ' || j.owner || ' holds SELECT outside its schema' AND c.object = j.rel)
    AND NOT (c.rule = '05 policy differs from template vetted_write' AND c.object IN (
      SELECT x.oid::regclass::text FROM (SELECT r.rel AS oid FROM kernel.rls_registry r WHERE r.vetted_org_policy
                                         UNION SELECT t.relid FROM kernel.rls_registry r, LATERAL pg_partition_tree(r.rel) t WHERE r.vetted_org_policy) x))
UNION ALL
SELECT v.rule, v.object FROM kernel.vetted_violations v
  WHERE NOT EXISTS (SELECT 1 FROM kernel.job_gate_grants j WHERE v.rule = '73 vetted role ' || j.owner || ' holds SELECT on a table that did not opt in' AND v.object = j.rel)
    AND NOT (v.rule = '57 write function uses ON CONFLICT' AND v.object IN (SELECT proc::text FROM kernel.function_registry WHERE dedupe_rel IS NOT NULL))
    AND NOT (v.rule = '57 write function uses ON CONFLICT' AND v.object IN (SELECT proc::text FROM kernel.vetted_write_pins WHERE op = 'upsert'))
UNION ALL
SELECT pv.rule, pv.object FROM kernel.person_violations pv
  WHERE NOT (pv.rule = '57 write function uses ON CONFLICT' AND pv.object IN (SELECT proc::text FROM kernel.function_registry WHERE dedupe_rel IS NOT NULL))
    AND NOT (pv.rule = '57 write function uses ON CONFLICT' AND pv.object IN (SELECT proc::text FROM kernel.vetted_write_pins WHERE op = 'upsert'))
    AND NOT (pv.rule = '88 read or write function deletes' AND pv.object IN (SELECT proc::text FROM kernel.vetted_write_pins WHERE op = 'delete'))
    AND NOT (pv.rule LIKE '89 vetted role % holds DELETE on a table without vetted_delete' AND pv.object IN (SELECT rel::text FROM kernel.vetted_write_pins WHERE op = 'delete'))
UNION ALL
SELECT rule, object FROM kernel.pin_violations
UNION ALL
SELECT rule, object FROM kernel.org_policy_violations"""


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("""DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM kernel.internal_policies) THEN
    RAISE EXCEPTION 'kernel_0018 downgrade: an internal policy is still registered; downgrade the module that registered it first';
  END IF;
END $$""")
    op.execute("DROP VIEW kernel.rls_violations")
    op.execute(_KERNEL_0017_VIEW)
    op.execute("REVOKE ALL ON kernel.rls_violations FROM PUBLIC")
    op.execute("DROP FUNCTION kernel.register_internal_policy(regclass, name), kernel._internal_policy_md5(regclass, name)")
    op.execute("DROP TABLE kernel.internal_policies")
