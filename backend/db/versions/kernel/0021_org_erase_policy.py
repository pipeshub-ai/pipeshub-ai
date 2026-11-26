"""kernel: an owner row policy on every org table that admits the rows of an org kernel.org_erase_allows names (an extension point; none here)"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "kernel_0021"
down_revision = "kernel_0020"
branch_labels = None
depends_on = None
older_code_compatible = "a policy for the owner role only, which admits no row while kernel.org_erase_allows answers false (always, here)"


def upgrade() -> None:
    run_sql(__file__)


def downgrade() -> None:
    # the revision before: kernel_0017's _secure_rel and kernel_0019's view, then every relation secured again without the policy
    from pathlib import Path
    here = Path(__file__).parent
    k17 = (here / "0017_vetted_org_policy.sql").read_text().splitlines()
    op.execute("\n".join(k17[123:186]))
    op.execute((here / "0019_monitor_role.sql").read_text().split("-- Any other membership of the role, or that one with other options, is still a violation.", 1)[1])
    op.execute("""DO $$ DECLARE r regclass; BEGIN
  FOR r IN SELECT g.rel FROM kernel.rls_registry g WHERE g.class IN ('org', 'lineage')
           UNION SELECT t.relid FROM kernel.rls_registry g, LATERAL pg_partition_tree(g.rel) t WHERE g.class IN ('org', 'lineage') LOOP
    EXECUTE format('DROP POLICY IF EXISTS org_erase ON %s', r);
  END LOOP; END $$""")
    op.execute("DROP FUNCTION kernel._org_erase_policy_ok(regclass), kernel.org_erase_allows(text)")
