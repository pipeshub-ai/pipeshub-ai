"""kernel: a managed platform's administrator (CREATEROLE, owns the database, not a superuser) is checked as the bootstrap superuser is"""
from pathlib import Path

from alembic import op
from db.lib.run_sql import run_sql

revision = "kernel_0024"
down_revision = "kernel_0023"
branch_labels = None
depends_on = None
older_code_compatible = ("the check views and _fresh_vetted_owner change only where the database owner is a non-superuser administrator; "
                         "with a superuser bootstrap kernel.bootstrap_admin() is NULL and every check answers as before")

_HERE = Path(__file__).parent


def upgrade() -> None:
    run_sql(__file__)


def downgrade() -> None:
    # kernel_0021's wrapper (the last one written as text), kernel_0004's warnings view and kernel_0023's _fresh_vetted_owner come
    # back as they were. In the five component views every call of kernel.bootstrap_admin() becomes NULL::oid, which turns each
    # exception off: no role is left out of the role list and checks 18 and 37 accept no administrator membership. One difference
    # stays on a declared managed platform: check 43 still reports an actor's own grant on a large-object function (only PUBLIC's
    # access is the platform's), which kernel_0023 did not; that grant is a violation on every platform.
    k21 = (_HERE / "0021_org_erase_policy.sql").read_text()
    wrapper = k21[k21.index("DROP VIEW kernel.rls_violations;"):]
    k23 = (_HERE / "0023_extension_owner.sql").read_text()
    fresh = k23[k23.index("CREATE FUNCTION kernel._fresh_vetted_owner"):k23.index("REVOKE ALL ON FUNCTION kernel._fresh_vetted_owner")]
    k04 = (_HERE / "0004_rls_violations.sql").read_text()
    warnings = k04[k04.index("CREATE VIEW kernel.rls_warnings"):k04.index("REVOKE ALL ON kernel.rls_warnings")]
    op.execute("DROP VIEW kernel.rls_violations")
    op.execute(r"""DO $$
DECLARE v name; def text;
BEGIN
  FOREACH v IN ARRAY ARRAY['rls_violations_core', 'vetted_violations', 'person_violations', 'pin_violations', 'org_policy_violations']::name[] LOOP
    def := replace(pg_get_viewdef(format('kernel.%I', v)::regclass), 'kernel.bootstrap_admin()', 'NULL::oid');
    EXECUTE format('CREATE OR REPLACE VIEW kernel.%I WITH (security_invoker = true) AS ', v) || def;
  END LOOP;
END $$""")
    op.execute(wrapper.replace("DROP VIEW kernel.rls_violations;", "", 1))
    op.execute("CREATE OR REPLACE " + warnings[len("CREATE "):])
    op.execute("CREATE OR REPLACE " + fresh[len("CREATE "):])
    op.execute("DROP FUNCTION kernel.bootstrap_admin()")
