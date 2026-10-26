"""Role bootstrap, the migration's behaviour without CREATEROLE, and the grant snapshot."""
import os
from pathlib import Path

from db import bootstrap_roles, surface

from .conftest import run_migrate

BASE = Path(__file__).resolve().parents[1] / "surface.snapshot.txt"


def test_bootstrap_is_idempotent_and_creates_no_privileged_role(bare_db):
    cluster, name = bare_db
    with cluster.admin(name) as c:
        made = bootstrap_roles.bootstrap(c, ["identity"], {"pipeshub_jobs": "pw"})
        assert {"pipeshub_vetted_identity", "pipeshub_vetted_kernel", "pipeshub_node_login"} <= set(made)     # the kernel schema always has its vetted owner
        assert bootstrap_roles.bootstrap(c, ["identity"]) == []                       # a second run changes nothing
        assert c.execute("SELECT count(*) FROM pg_roles WHERE rolname LIKE 'pipeshub\\_%' AND (rolsuper OR rolcreaterole OR rolbypassrls "
                         "OR rolcreatedb OR rolreplication)").fetchone()[0] == 0
        assert c.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname = 'pipeshub_restore'").fetchone()[0] is False
        assert c.execute("SELECT rolpassword IS NOT NULL FROM pg_authid WHERE rolname = 'pipeshub_jobs'").fetchone()[0] is True
        assert c.execute("SELECT NOT has_database_privilege('pipeshub_query', current_database(), 'TEMP')").fetchone()[0]


def test_migrate_without_createrole_names_the_bootstrap_when_roles_are_missing(db):
    cluster, name = db
    with cluster.admin() as c:
        c.execute("DROP ROLE pipeshub_ro")
    r = run_migrate(cluster.url(name, "pipeshub_migrate"))
    assert r.returncode == 1 and "python -m db.bootstrap_roles" in r.stdout, r.stdout


def test_surface_matches_the_committed_text(db):
    cluster, name = db
    update = os.environ.get("UPDATE_SURFACE") == "1"
    r = run_migrate(cluster.url(name, "pipeshub_migrate"), PG_MIGRATE_SURFACE_CHECK="skip" if update else "on")
    assert r.returncode == 0, r.stdout
    with cluster.admin(name) as c:
        live = surface.live_lines(c)
        version = surface.major(c)
    if update:        # lines of the other PostgreSQL majors are kept
        keep = [x for x in BASE.read_text(encoding="utf-8").splitlines() if x not in surface.for_major([x], version)]
        BASE.write_text("\n".join(sorted([*keep, *live])) + "\n", encoding="utf-8")
    assert live == sorted(surface.for_major(surface.expected_lines([BASE]), version)), "the security surface changed: review the diff, then rerun with UPDATE_SURFACE=1"


def test_surface_diff_reports_extra_and_missing_lines(db):
    cluster, name = db
    assert run_migrate(cluster.url(name, "pipeshub_migrate")).returncode == 0
    with cluster.admin(name) as c:
        c.execute("GRANT SELECT ON kernel.rls_registry TO pipeshub_node; GRANT pipeshub_jobs TO pipeshub_node")
        found = {line for _, line in surface.violations(c)}
    assert "relacl kernel.rls_registry pipeshub_node SELECT grantable=false" in found
    assert "member pipeshub_node -> pipeshub_jobs inherit=true set=true admin=false" in found
    with cluster.admin(name) as c:
        c.execute("REVOKE SELECT ON kernel.schema_epoch FROM pipeshub_node")
        assert ("50 surface: missing", "relacl kernel.schema_epoch pipeshub_node SELECT grantable=false") in surface.violations(c)


def test_sysfuncs_hash_ignores_function_bodies_and_library_paths(db):
    cluster, name = db
    with cluster.admin(name) as c:
        query = next(t for k, t in surface.QUERIES if k == "sysfuncs")
        c.execute("SET search_path = pg_catalog")     # as live_lines does
        before = c.execute(query).fetchone()[0]
        c.execute("UPDATE pg_proc SET prosrc = prosrc || ' ', probin = 'elsewhere/lib' WHERE oid < 16384 AND prolang IN (12, 13, 14)")
        assert c.execute(query).fetchone()[0] == before
        c.execute("UPDATE pg_proc SET provolatile = 'v' WHERE proname = 'lower' AND provolatile <> 'v'")
        assert c.execute(query).fetchone()[0] != before
        c.rollback()
