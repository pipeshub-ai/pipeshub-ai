"""Role bootstrap, the migration's behaviour without CREATEROLE, and the grant snapshot."""
import hashlib
import os
from pathlib import Path

import psycopg
import pytest

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


def test_bootstrap_refuses_a_login_without_password_unless_waived(bare_db):
    cluster, name = bare_db
    with cluster.admin(name) as c:
        with pytest.raises(bootstrap_roles.MissingPassword, match="PG_NODE_PASSWORD.*PG_BOOTSTRAP_ALLOW_NO_PASSWORD"):
            bootstrap_roles.bootstrap(c, [], {"pipeshub_jobs": "pw"}, allow_no_password=False)
        assert c.execute("SELECT count(*) FROM pg_roles WHERE rolname = 'pipeshub_jobs'").fetchone()[0] == 0   # nothing was created
        all_pw = dict.fromkeys(bootstrap_roles.PASSWORD_VARS, "pw")
        assert "pipeshub_node_login" in bootstrap_roles.bootstrap(c, [], all_pw, allow_no_password=False)         # passwords: as before
        assert bootstrap_roles.bootstrap(c, [], allow_no_password=False) == []                                    # existing roles are not asked again


def test_bootstrap_waiver_creates_a_login_without_password(bare_db):
    cluster, name = bare_db
    with cluster.admin(name) as c:
        bootstrap_roles.bootstrap(c, [], allow_no_password=True)
        assert c.execute("SELECT rolpassword IS NULL FROM pg_authid WHERE rolname = 'pipeshub_node_login'").fetchone()[0]


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
    if update:        # lines of the other PostgreSQL majors are kept, and the reviewed sysfuncs lines of other minor releases
        keep = [x for x in BASE.read_text(encoding="utf-8").splitlines() if x not in surface.for_major([x], version) or x.startswith(("sysfuncs@", "? "))]
        BASE.write_text("\n".join(sorted({*keep, *live})) + "\n", encoding="utf-8")
    assert surface.diff(live, surface.for_major(surface.expected_lines([BASE]), version)) == [], "the security surface changed: review the diff, then rerun with UPDATE_SURFACE=1"


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


def test_sysfuncs_rows_are_what_the_hash_is_taken_over(db):
    cluster, name = db
    with cluster.admin(name) as c:
        rows = surface.sysfunc_rows(c)
        c.execute("SET search_path = pg_catalog")     # as live_lines does
        line = c.execute(next(t for k, t in surface.QUERIES if k == "sysfuncs")).fetchone()[0]
    digest = hashlib.md5("\n".join(rows).encode()).hexdigest()
    assert line == f"count={len(rows)} md5={digest}", line


def test_the_monitoring_role_reads_the_statistics_and_nothing_of_ours(db):
    cluster, name = db
    with cluster.admin(name) as c:
        assert c.execute("SELECT to_regrole('pipeshub_monitor') IS NULL").fetchone()[0]           # opt-in: the default bootstrap makes none
        assert bootstrap_roles.bootstrap(c, [], {"pipeshub_monitor": "pw"}) == ["pipeshub_monitor"]
        assert bootstrap_roles.bootstrap(c, [], {"pipeshub_monitor": "pw"}) == []                  # and a second run changes nothing
    r = run_migrate(cluster.url(name, "pipeshub_migrate"))                                       # catalog and surface checks accept it
    assert r.returncode == 0, r.stdout
    with cluster.admin(name) as admin, psycopg.connect(host="127.0.0.1", port=cluster.port, user="pipeshub_monitor", dbname=name,
                                                       autocommit=True) as mon:
        admin.execute("SELECT 'busy'")
        pid = admin.info.backend_pid
        # pg_read_all_stats: another session's statement; pg_read_all_settings: a superuser-only setting; pg_monitor: the WAL directory
        assert mon.execute("SELECT query FROM pg_stat_activity WHERE pid = %s", (pid,)).fetchone()[0] == "SELECT 'busy'"
        assert mon.execute("SELECT current_setting('data_directory') <> '', count(*) >= 0 FROM pg_ls_waldir()").fetchone() == (True, True)
        assert mon.execute("SELECT count(*) FROM pg_stat_user_tables WHERE schemaname = 'platform'").fetchone()[0] > 0
        ours = [n for (n,) in admin.execute("SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg\\_%' AND nspname NOT IN "
                                            "('information_schema', 'public')")]
        assert ours and [n for n in ours if mon.execute("SELECT has_schema_privilege(%s, 'USAGE')", (n,)).fetchone()[0]] == []
        assert admin.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = ANY(%s) "
                             "AND has_table_privilege('pipeshub_monitor', c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')",
                             (ours,)).fetchone()[0] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            mon.execute("SELECT * FROM platform.kv_entries LIMIT 1")
        assert mon.execute("SELECT pg_has_role('pg_read_all_data', 'USAGE') OR pg_has_role('pg_write_all_data', 'USAGE')").fetchone()[0] is False
    with cluster.admin(name) as c:                                     # any other membership, or that one with SET, is still a violation
        c.execute("GRANT pg_read_all_data TO pipeshub_monitor")
        c.execute("GRANT pg_monitor TO pipeshub_monitor WITH SET TRUE")
        found = {rule for rule, _ in c.execute("SELECT rule, object FROM kernel.rls_violations")}
        assert {"37 unexpected membership pipeshub_monitor -> pg_read_all_data", "37 unexpected membership pipeshub_monitor -> pg_monitor"} <= found
        assert "member pipeshub_monitor -> pg_read_all_data inherit=true set=true admin=false" in {line for _, line in surface.violations(c)}
