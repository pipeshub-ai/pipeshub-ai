"""kernel: check 37 accepts the optional monitoring role's one membership (pg_monitor, nothing of ours), for a metrics exporter"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "kernel_0019"
down_revision = "kernel_0018"
branch_labels = None
depends_on = None
older_code_compatible = ("the check view accepts one more membership, of a role that holds no privilege of ours; "
                         "no table, function or grant earlier code uses changes")

_KERNEL_0018_VIEW = """CREATE VIEW kernel.rls_violations WITH (security_invoker = true) AS
SELECT c.rule, c.object FROM kernel.rls_violations_core c
  WHERE NOT (c.rule = '42 vetted role pipeshub_vetted_kernel holds SELECT outside its schema' AND c.object IN (SELECT rel::text FROM kernel.vetted_extension_reads))
    AND NOT EXISTS (SELECT 1 FROM kernel.job_gate_grants j WHERE c.rule = '42 vetted role ' || j.owner || ' holds SELECT outside its schema' AND c.object = j.rel)
    AND NOT (c.rule = '05 policy differs from template vetted_write' AND c.object IN (
      SELECT x.oid::regclass::text FROM (SELECT r.rel AS oid FROM kernel.rls_registry r WHERE r.vetted_org_policy
                                         UNION SELECT t.relid FROM kernel.rls_registry r, LATERAL pg_partition_tree(r.rel) t WHERE r.vetted_org_policy) x))
    AND NOT EXISTS (SELECT 1 FROM kernel.internal_policies ip JOIN kernel.rls_registry g ON g.rel = ip.rel AND g.class = 'internal'
                    WHERE c.rule = '04 unexpected policy ' || ip.polname AND c.object = ip.rel::text)
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
SELECT rule, object FROM kernel.org_policy_violations
UNION ALL -- 140 a registered internal policy is gone, or its table is no longer internal
SELECT '140 registered internal policy is missing: ' || ip.polname, ip.rel::text FROM kernel.internal_policies ip
  WHERE kernel._internal_policy_md5(ip.rel, ip.polname) IS NULL
     OR NOT EXISTS (SELECT 1 FROM kernel.rls_registry g WHERE g.rel = ip.rel AND g.class = 'internal')
UNION ALL -- 141 edited command, roles, expression or a RESTRICTIVE/permissive flip
SELECT '141 internal policy differs from its registration: ' || ip.polname, ip.rel::text FROM kernel.internal_policies ip
  WHERE kernel._internal_policy_md5(ip.rel, ip.polname) <> ip.def_md5"""


def upgrade():
    run_sql(__file__)


def downgrade():
    # a monitoring role the bootstrap made stays, and the restored view reports its membership again
    op.execute("DROP VIEW kernel.rls_violations")
    op.execute(_KERNEL_0018_VIEW)
    op.execute("REVOKE ALL ON kernel.rls_violations FROM PUBLIC")
