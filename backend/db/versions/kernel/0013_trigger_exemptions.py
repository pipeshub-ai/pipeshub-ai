"""kernel: reviewed exemptions for trigger functions that write (the document-store change log)"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "kernel_0013"
down_revision = "kernel_0012"
branch_labels = None
depends_on = None
older_code_compatible = "older code never calls the new exemption table or function, and the replaced check function keeps its signature and result and only stops reporting the exempted trigger"

# check 97 (kernel.person_violations): a trigger function fires without an EXECUTE check, so it counts as callable by PUBLIC and may
# not write. An exemption is reviewed, names the function, and is pinned to the md5 of its body: change the body and the check applies again.
WRITES = "\\M'::text) AND (EXISTS ( SELECT 1\n                   FROM aclexplode(i.acl) x(grantor, grantee, privilege_type, is_grantable)"
EXEMPT = ("\\M'::text) AND (NOT (EXISTS ( SELECT 1\n                   FROM kernel.trigger_exemptions te\n"
          "                  WHERE (((te.proc)::oid = i.oid) AND (te.body_md5 = md5(i.prosrc)))))) AND (EXISTS ( SELECT 1\n"
          "                   FROM aclexplode(i.acl) x(grantor, grantee, privilege_type, is_grantable)")


def _view(replace_from: str, replace_to: str) -> None:
    conn = op.get_bind()
    v = conn.exec_driver_sql("SELECT pg_get_viewdef('kernel.person_violations'::regclass)").scalar()
    if v.count(replace_from) != 1:
        raise RuntimeError("kernel_0013: check 97 was not found in kernel.person_violations")
    conn.exec_driver_sql("CREATE OR REPLACE VIEW kernel.person_violations WITH (security_invoker = true) AS " + v.replace(replace_from, replace_to).replace("%", "%%"))


def upgrade():
    run_sql(__file__)
    _view(WRITES, EXEMPT)


def downgrade():
    _view(EXEMPT, WRITES)
    op.execute("DROP FUNCTION kernel.exempt_trigger(regprocedure, text, text)")
    op.execute("DROP TABLE kernel.trigger_exemptions")
