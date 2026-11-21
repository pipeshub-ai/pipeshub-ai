"""Role bootstrap, the migration's behaviour without CREATEROLE, and the grant snapshot."""
import hashlib
import os
from pathlib import Path

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


def _edition_registering(tmp_path, schemas, module_dir=None):
    ed = tmp_path / "edition.py"
    locations = [str(module_dir)] if module_dir else []
    ed.write_text(f"EXTRA_MIGRATION_LOCATIONS = {locations!r}\nEXTRA_VETTED_SCHEMAS = {list(schemas)!r}\n")
    return str(ed)


def test_bootstrap_creates_the_vetted_owner_of_a_registered_module_with_no_flag(bare_db, tmp_path, monkeypatch):
    cluster, name = bare_db
    monkeypatch.setenv("PIPESHUB_EDITION_FILE", _edition_registering(tmp_path, ["zz_probe"]))
    with cluster.admin(name) as c:
        made = bootstrap_roles.bootstrap(c, ["identity"])                         # the caller names no probe schema
        assert "pipeshub_vetted_zz_probe" in made
        assert bootstrap_roles.bootstrap(c, ["identity", "zz_probe"]) == []         # naming it as well changes nothing
        row = c.execute("SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreaterole FROM pg_roles WHERE rolname = 'pipeshub_vetted_zz_probe'").fetchone()
        assert tuple(row) == (False, False, False, False)
        # the owner role may hand the vetted role out (ADMIN) but never use it
        assert tuple(c.execute("SELECT admin_option, inherit_option, set_option FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid "
                               "JOIN pg_roles u ON u.oid = m.member WHERE r.rolname = 'pipeshub_vetted_zz_probe' AND u.rolname = 'pipeshub_owner'"
                               ).fetchone()) == (True, False, False)


def test_bootstrap_without_a_registered_schema_adds_no_role(bare_db, tmp_path, monkeypatch):
    cluster, name = bare_db
    for edition in (_edition_registering(tmp_path, []), str(tmp_path / "missing.py")):      # none registered, and no edition file at all
        monkeypatch.setenv("PIPESHUB_EDITION_FILE", edition)
        with cluster.admin(name) as c:
            bootstrap_roles.bootstrap(c, [])
            assert c.execute("SELECT count(*) FROM pg_roles WHERE rolname LIKE 'pipeshub\\_vetted\\_%'").fetchone()[0] == len(bootstrap_roles.SHIPPED_VETTED)


def test_bootstrap_refuses_a_bad_registered_schema_name_before_creating_anything(bare_db, tmp_path, monkeypatch):
    cluster, name = bare_db
    monkeypatch.setenv("PIPESHUB_EDITION_FILE", _edition_registering(tmp_path, ["zz_probe", "Bad;name"]))
    with cluster.admin(name) as c:
        with pytest.raises(ValueError, match="not a schema name"):
            bootstrap_roles.bootstrap(c, [])
        assert c.execute("SELECT count(*) FROM pg_roles WHERE rolname LIKE 'pipeshub\\_%'").fetchone()[0] == 0


def test_a_registered_module_migrates_after_the_ordinary_bootstrap_and_stops_without_the_seam(bare_db, tmp_path, monkeypatch):
    """The module's first revision needs its vetted owner (the migrate login cannot create roles): with the schema registered the plain
    bootstrap makes it, so ``db.migrate`` needs no flag; with the registration of the schema removed the same migrate stops and says so."""
    cluster, name = bare_db
    mod = tmp_path / "zz_probe"
    mod.mkdir()
    (mod / "0001_create_schema.py").write_text(
        'from alembic import op\n'
        'revision = "zz_probe_0001"\ndown_revision = None\nbranch_labels = ("zz_probe",)\ndepends_on = "kernel_0010"\n'
        'older_code_compatible = "adds a schema only"\n'
        'def upgrade():\n'
        '    op.execute("""DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = \'pipeshub_vetted_zz_probe\') THEN\n'
        '      RAISE EXCEPTION \'the vetted owner of schema zz_probe is missing\'; END IF; END $$""")\n'
        '    op.execute("CREATE SCHEMA zz_probe")\n'
        'def downgrade():\n    op.execute("DROP SCHEMA zz_probe")\n')
    registered = _edition_registering(tmp_path, ["zz_probe"], mod)
    unregistered = tmp_path / "edition_unregistered.py"
    unregistered.write_text(f"EXTRA_MIGRATION_LOCATIONS = {[str(mod)]!r}\n")
    env = {"PG_MIGRATE_SURFACE_CHECK": "skip"}                                    # the probe module ships no surface file
    with cluster.admin(name) as c:
        monkeypatch.setenv("PIPESHUB_EDITION_FILE", str(unregistered))
        bootstrap_roles.bootstrap(c, [], extensions=["vector", "pg_trgm"])
    r = run_migrate(cluster.url(name, "pipeshub_migrate"), PIPESHUB_EDITION_FILE=str(unregistered), **env)
    assert r.returncode != 0 and "the vetted owner of schema zz_probe is missing" in r.stdout + r.stderr, r.stdout + r.stderr
    with cluster.admin(name) as c:
        monkeypatch.setenv("PIPESHUB_EDITION_FILE", registered)
        assert "pipeshub_vetted_zz_probe" in bootstrap_roles.bootstrap(c, [], extensions=["vector", "pg_trgm"])     # no --vetted
    r = run_migrate(cluster.url(name, "pipeshub_migrate"), PIPESHUB_EDITION_FILE=registered, **env)
    assert r.returncode == 0, r.stdout + r.stderr


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
        keep = [x for x in BASE.read_text(encoding="utf-8").splitlines() if x not in surface.for_major([x], version) or x.startswith("sysfuncs@")]
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
