"""Pinned vetted writes: the revision that installs them, the pre-pin fallback it revokes, and the epoch rules around it.

A module revises in two steps the way the kg branch will: ``kx_0001`` creates an access table and, for shadow mode, lets the connectors
role write it directly (the pre-pin fallback); ``kx_0002`` installs the vetted writers with their pins, revokes the fallback and is a
contract (``min_code_epoch = 2``: code built before it must not run). The kernel refuses the run when the revoke is forgotten (rule 110),
the migration lint refuses a revoke without the contract, and an older build refuses the database it finds."""
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml
from psycopg import errors as pg

from db import boot_assert, bootstrap_roles, lint
from db.lib import vetted

from .conftest import BACKEND, EXTENSIONS, run_migrate
from .rls_fixture import ORG_A, ORG_B
from .test_integration import (
    HEADS,
    REVISION_HEADS,
    _applied,
    connect,
    edition_with,
    epochs,
)

MIGRATE = "pipeshub_migrate"

MANIFEST = {"functions": [
    {"name": "kx.w_members_set", "class": "write", "scope": "org", "writer": "pipeshub_connectors", "custom_plan": True,
     "callers": ["python/app/services/graph_db/postgres/kg_write.py"], "pins": [{"op": "upsert", "rel": "kx.scope_members", "set": ["role"]}],
     "args": [{"name": "p_principals", "type": "bigint[]", "max_items": 5000, "item_not_null": True},
              {"name": "p_nodes", "type": "bigint[]", "max_items": 5000, "item_not_null": True},
              {"name": "p_roles", "type": "smallint[]", "max_items": 5000, "item_not_null": True, "item_enum": [1, 2]}],
     "returns": "int", "no_org": "0", "reads": ["kx.scope_members"],
     "body": textwrap.dedent("""\
        DECLARE v_n integer;
        BEGIN
          IF cardinality(p_nodes) <> cardinality(p_principals) OR cardinality(p_roles) <> cardinality(p_principals) THEN
            RAISE EXCEPTION 'w_members_set: the arrays differ in length' USING ERRCODE = '22023';
          END IF;
          INSERT INTO kx.scope_members (org_id, principal_sk, node_sk, role) SELECT v_org, k.a, k.b, k.c FROM unnest(p_principals, p_nodes, p_roles) AS k(a, b, c)
            ON CONFLICT (org_id, principal_sk, node_sk) DO UPDATE SET role = excluded.role;
          GET DIAGNOSTICS v_n = ROW_COUNT;
          RETURN v_n;
        END;"""), "test": {}},
    {"name": "kx.w_members_delete", "class": "write", "scope": "org", "writer": "pipeshub_connectors", "custom_plan": True,
     "callers": ["python/app/services/graph_db/postgres/kg_write.py"], "pins": [{"op": "delete", "rel": "kx.scope_members", "key": ["principal_sk", "node_sk"]}],
     "args": [{"name": "p_principals", "type": "bigint[]", "max_items": 5000, "item_not_null": True},
              {"name": "p_nodes", "type": "bigint[]", "max_items": 5000, "item_not_null": True}],
     "returns": "int", "no_org": "0", "reads": ["kx.scope_members"],
     "body": textwrap.dedent("""\
        DECLARE v_n integer;
        BEGIN
          IF cardinality(p_nodes) <> cardinality(p_principals) THEN
            RAISE EXCEPTION 'w_members_delete: the arrays differ in length' USING ERRCODE = '22023';
          END IF;
          DELETE FROM kx.scope_members t USING unnest(p_principals, p_nodes) AS k(a, b) WHERE t.org_id = v_org AND t.principal_sk = k.a AND t.node_sk = k.b;
          GET DIAGNOSTICS v_n = ROW_COUNT;
          RETURN v_n;
        END;"""), "test": {}},
]}

TABLE = """CREATE SCHEMA kx;
CREATE TABLE kx.scope_members (org_id text NOT NULL, principal_sk bigint NOT NULL, node_sk bigint NOT NULL, role smallint NOT NULL,
  PRIMARY KEY (org_id, principal_sk, node_sk));
SELECT kernel.apply_rls('kx.scope_members', 'org', p_vetted_write => true);
GRANT USAGE ON SCHEMA kx TO pipeshub_app, pipeshub_jobs, pipeshub_connectors_login, pipeshub_query_login, pipeshub_indexing_login, pipeshub_node_login;
GRANT SELECT ON kx.scope_members TO pipeshub_connectors, pipeshub_query, pipeshub_indexing, pipeshub_jobs;
-- the pre-pin fallback (shadow mode): the connectors role writes the table directly, nobody else
GRANT INSERT, UPDATE, DELETE ON kx.scope_members TO pipeshub_connectors;
"""
REVOKE = "REVOKE INSERT, UPDATE, DELETE ON kx.scope_members FROM pipeshub_connectors;\n"

PY1 = '''
from db.lib.run_sql import run_sql
revision = "kx_0001"
down_revision = None
branch_labels = ("kx",)
depends_on = "kernel_0017"
older_code_compatible = "a new schema nothing else reads"
def upgrade():
    run_sql(__file__)
def downgrade():
    pass
'''
PY2 = '''
from alembic import op
from db.lib.run_sql import run_sql
from db.lib.vetted import run_vetted
revision = "kx_0002"
down_revision = "kx_0001"
branch_labels = None
depends_on = None
{epoch}
def upgrade():
    run_sql(__file__)
    run_vetted(__file__, "w_members_set", "w_members_delete")
def downgrade():
    pass
'''


def write_module(root: Path, *, second: bool = True, revoke: bool = True, contract: bool = True) -> Path:
    """``root/kx``: revision 1 (table + fallback), and optionally revision 2 (writers + pins + revoke + contract)."""
    d = root / "kx"
    (d / "vetted").mkdir(parents=True)
    (d / "0001_tables.py").write_text(PY1)
    (d / "0001_tables.sql").write_text(TABLE)
    if second:
        (d / "vetted" / "manifest.yaml").write_text(yaml.safe_dump(MANIFEST))
        for name, sql in vetted.render_all(vetted.load(d / "vetted" / "manifest.yaml")).items():
            (d / "vetted" / name).write_text(sql)
        (d / "0002_writers.py").write_text(PY2.format(epoch="min_code_epoch = 2" if contract else "older_code_compatible = 'a claim'"))
        (d / "0002_writers.sql").write_text("SELECT kernel.require_vetted_org_policy('kx.scope_members');\n" + (REVOKE if revoke else ""))
    return d


def seeded_cluster(db):
    cluster, name = db
    with cluster.admin(name) as c:
        bootstrap_roles.bootstrap(c, ["kx"], extensions=EXTENSIONS)
    return cluster, name


def as_unit(cluster, name, role: str, org: str, sql: str, args=()):
    with cluster.admin(name) as c:
        c.execute("BEGIN")
        try:
            c.execute(f"SET LOCAL ROLE {role}")
            c.execute("SELECT set_config('app.org_id', %s, true)", (org,))
            cur = c.execute(sql, args)
            return cur.fetchall() if cur.description else None
        finally:
            c.execute("ROLLBACK")


# ---- the revision that installs the writers revokes the fallback ----------------------------------------------------------------

def test_the_writers_revision_revokes_the_fallback_keeps_the_data_and_is_a_contract(db, tmp_path):
    cluster, name = seeded_cluster(db)
    url = cluster.url(name, MIGRATE)
    old, new = write_module(tmp_path / "old", second=False), write_module(tmp_path / "new")
    r = run_migrate(url, **edition_with(tmp_path, old))
    assert r.returncode == 0, r.stdout
    assert epochs(cluster, name)["kx"] == (1, 0)
    with cluster.admin(name) as c:                                       # shadow mode: the connectors role writes directly
        c.execute("BEGIN")
        c.execute("SET LOCAL ROLE pipeshub_connectors")
        c.execute("SELECT set_config('app.org_id', %s, true)", (ORG_A,))
        c.execute("INSERT INTO kx.scope_members VALUES (%s, 1, 1, 1), (%s, 2, 2, 1)", (ORG_A, ORG_A))
        c.execute("COMMIT")
        c.execute("INSERT INTO kx.scope_members VALUES (%s, 1, 1, 2)", (ORG_B,))

    r = run_migrate(url, **edition_with(tmp_path, new))
    assert r.returncode == 0, r.stdout
    assert epochs(cluster, name)["kx"] == (2, 2) and epochs(cluster, name)["kernel"] == (17, 11)
    with cluster.admin(name) as c:
        assert c.execute("SELECT org_id, count(*) FROM kx.scope_members GROUP BY 1 ORDER BY 1").fetchall() == [(ORG_A, 2), (ORG_B, 1)]       # the data came through
        assert c.execute("SELECT rule, object FROM kernel.rls_violations").fetchall() == []
        pins = c.execute("SELECT proc::text, op, key_cols, set_cols FROM kernel.vetted_write_pins WHERE proc::text LIKE 'kx.%' ORDER BY 1").fetchall()
        assert pins == [("kx.w_members_delete(bigint[],bigint[])", "delete", ["principal_sk", "node_sk"], None),
                        ("kx.w_members_set(bigint[],bigint[],smallint[])", "upsert", None, ["role"])]
    for sql in ("INSERT INTO kx.scope_members VALUES (%s, 9, 9, 1)", "UPDATE kx.scope_members SET role = 2", "DELETE FROM kx.scope_members"):      # the fallback is gone
        with pytest.raises(pg.InsufficientPrivilege):
            as_unit(cluster, name, "pipeshub_connectors", ORG_A, sql, (ORG_A,) if "%s" in sql else ())
    assert as_unit(cluster, name, "pipeshub_connectors", ORG_A, "SELECT kx.w_members_set(%s, %s, %s)", ([5], [6], [1])) == [(1,)]       # the writers work
    assert as_unit(cluster, name, "pipeshub_connectors", ORG_A, "SELECT kx.w_members_delete(%s, %s)", ([1, 2], [1, 2])) == [(2,)]
    # the contract: code built before kx_0002 must not run
    rows = {b: (e, m) for b, e, m in connect(cluster, name).execute("SELECT branch, epoch, min_code_epoch FROM kernel.schema_epoch")}
    assert boot_assert.schema_problems(rows, {"kx": {"requires": 1, "code": 1}}) == ["schema kx min_code_epoch 2 > code 1"]
    assert boot_assert.schema_problems(rows, {"kx": {"requires": 2, "code": 2}, "kernel": {"requires": 17, "code": 17}}) == []
    assert boot_assert.schema_problems(rows, {"kernel": {"requires": 15, "code": 15}}) == []            # kernel_0016 and 0017 are not a contract: the previous build still runs


def test_forgetting_to_revoke_the_fallback_fails_the_run_and_leaves_the_database_as_it_was(db, tmp_path):
    cluster, name = seeded_cluster(db)
    url = cluster.url(name, MIGRATE)
    assert run_migrate(url, **edition_with(tmp_path, write_module(tmp_path / "old", second=False))).returncode == 0
    before = _applied(cluster, name)
    r = run_migrate(url, **edition_with(tmp_path, write_module(tmp_path / "new", revoke=False)))
    assert r.returncode == 4, r.stdout
    assert "110 pipeshub_connectors holds DELETE" in r.stdout and "110 pipeshub_connectors holds INSERT" in r.stdout and "110 pipeshub_connectors holds UPDATE" in r.stdout
    assert _applied(cluster, name) == before                                                               # nothing of kx_0002 stayed
    with cluster.admin(name) as c:
        assert c.execute("SELECT count(*) FROM kernel.vetted_write_pins WHERE rel::text LIKE 'kx.%'").fetchone()[0] == 0
        assert c.execute("SELECT to_regprocedure('kx.w_members_set(bigint[],bigint[],smallint[])') IS NULL").fetchone()[0]


def test_the_migration_lint_wants_the_contract_for_the_revoke(tmp_path):
    ok = write_module(tmp_path / "ok")
    assert lint.lint_file(ok / "0002_writers.sql") == []
    bad = write_module(tmp_path / "bad", contract=False)
    got = lint.lint_file(bad / "0002_writers.sql")
    assert got and any("REVOKE" in g or "min_code_epoch" in g for g in got), got


def test_an_older_build_refuses_a_database_where_the_writers_revision_ran(db, tmp_path):
    """helm rollback to a build that has kx_0001 only: the database holds kx_0002, a contract above that build's code epoch."""
    cluster, name = seeded_cluster(db)
    url = cluster.url(name, MIGRATE)
    old, new = write_module(tmp_path / "old", second=False), write_module(tmp_path / "new")
    assert run_migrate(url, **edition_with(tmp_path, new)).returncode == 0
    r = run_migrate(url, **edition_with(tmp_path, old))
    assert r.returncode == 2 and "cannot run against" in r.stdout and "roll forward" in r.stdout and "kx" in r.stdout, r.stdout
    assert epochs(cluster, name)["kx"] == (2, 2)


def test_a_build_with_the_writers_runs_again_on_its_own_database(db, tmp_path):
    cluster, name = seeded_cluster(db)
    url = cluster.url(name, MIGRATE)
    env = edition_with(tmp_path, write_module(tmp_path / "new"))
    assert run_migrate(url, **env).returncode == 0
    before = _applied(cluster, name)
    assert run_migrate(url, **env).returncode == 0 and _applied(cluster, name) == before


# ---- kernel_0016 and kernel_0017 themselves: from the previous revision, and back ----------------------------------------------------------------------

def test_the_kernel_downgrades_to_the_previous_release_revision_and_upgrades_again(db):
    cluster, name = db
    url = cluster.url(name, MIGRATE)
    assert run_migrate(url).returncode == 0
    assert epochs(cluster, name)["kernel"] == (17, 11)
    r = run_migrate(url, "downgrade", "kernel_0015", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert epochs(cluster, name)["kernel"] == (15, 11)                                  # min_code_epoch never decreases, and it was 11 already
    with connect(cluster, name) as c:
        assert c.execute("SELECT to_regclass('kernel.vetted_write_pins') IS NULL AND to_regclass('kernel.pin_violations') IS NULL").fetchone()[0]
        assert c.execute("SELECT to_regprocedure('kernel.allow_vetted_write_op(regprocedure,regclass,text,name[],name[],name,integer)') IS NULL").fetchone()[0]
        assert c.execute("SELECT rule, object FROM kernel.rls_violations").fetchall() == []
        assert "pin_violations" not in c.execute("SELECT pg_get_viewdef('kernel.rls_violations'::regclass)").fetchone()[0]
    r = run_migrate(url)                                                                 # forward again, with the surface check on
    assert r.returncode == 0, r.stdout + r.stderr
    assert epochs(cluster, name) == HEADS
    with connect(cluster, name) as c:
        assert c.execute("SELECT rule, object FROM kernel.rls_violations").fetchall() == []
        assert {v for (v,) in c.execute("SELECT version_num FROM kernel.alembic_version")} == REVISION_HEADS


def test_the_kernel_does_not_go_back_while_a_pin_exists(db, tmp_path):
    cluster, name = seeded_cluster(db)
    url = cluster.url(name, MIGRATE)
    env = edition_with(tmp_path, write_module(tmp_path / "new"))
    assert run_migrate(url, **env).returncode == 0
    r = run_migrate(url, "downgrade", "kernel_0015", PG_MIGRATE_ALLOW_DOWNGRADE="1", **env)       # kx_0002's downgrade leaves its pins
    assert r.returncode != 0 and ("per-org vetted policy" in r.stdout + r.stderr), r.stdout + r.stderr       # kernel_0017 goes first; kernel_0016 then refuses on the pins
    r = run_migrate(url, "downgrade", "kernel_0016", PG_MIGRATE_ALLOW_DOWNGRADE="1", **env)
    assert r.returncode != 0 and "per-org vetted policy" in r.stdout + r.stderr, r.stdout + r.stderr
    assert epochs(cluster, name)["kernel"] == (17, 11)
    with connect(cluster, name) as c:
        assert c.execute("SELECT count(*) FROM kernel.vetted_write_pins WHERE rel::text LIKE 'kx.%'").fetchone()[0] == 2
        assert c.execute("SELECT rule, object FROM kernel.rls_violations").fetchall() == []


def _previous_release(tmp_path: Path) -> Path | None:
    """backend/db as it was before kernel_0016 (the first of the pinned-writes revisions) (the parent of the commit that added it), when the history has it, with the retrieval
    revisions from 0012 on removed (the branches that carry a revision of that number do not agree on what it is)."""
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=BACKEND, capture_output=True, text=True, check=True).stdout.strip()
        sha = subprocess.run(["git", "log", "--diff-filter=A", "--format=%H", "-1", "--", "backend/db/versions/kernel/0016_vetted_write_pins.sql"],
                             cwd=top, capture_output=True, text=True, check=True).stdout.strip()
        if not sha:
            return None
        dest = tmp_path / "prev"
        dest.mkdir()
        tar = subprocess.run(["git", "archive", f"{sha}^", "backend/db"], cwd=top, capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(dest)], input=tar, check=True)
        for late in (dest / "backend" / "db" / "versions" / "retrieval").glob("*"):          # a revision of the retrieval branch from 0012 on belongs to a later build: lines of this
            if late.name[:4].isdigit() and int(late.name[:4]) >= 12:                          # history number it differently, and the previous release must hold none of them
                late.unlink()
        return dest / "backend"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def test_upgrade_from_the_previous_release_with_data_in_place(db, tmp_path):
    prev = _previous_release(tmp_path)
    if prev is None:
        pytest.skip("the history does not hold the commit before kernel_0016 (the first of the pinned-writes revisions)")
    cluster, name = db
    url = cluster.url(name, MIGRATE)
    r = run_migrate(url, PYTHONPATH=str(prev), PG_MIGRATE_SURFACE_CHECK="skip")                  # the previous build's tree migrates first
    assert r.returncode == 0 and "epoch kernel=15" in r.stdout, r.stdout + r.stderr
    r = run_migrate(url)
    assert r.returncode == 0 and "epoch kernel=17 min_code=11" in r.stdout, r.stdout + r.stderr
    assert epochs(cluster, name) == HEADS
    # the previous build, started against the upgraded database, finds nothing to do (it would be a helm rollback)
    r = run_migrate(url, PYTHONPATH=str(prev), PG_MIGRATE_SURFACE_CHECK="skip")
    out = r.stdout + r.stderr          # nothing to do, or (a contract revision above its code epoch: the retrieval branch's) a refusal that says what to do
    assert (r.returncode == 0 and "database is ahead of this build" in out) or (r.returncode == 2 and "cannot run against" in out and "retrieval" in out), out
    rows = {b: (e, m) for b, e, m in connect(cluster, name).execute("SELECT branch, epoch, min_code_epoch FROM kernel.schema_epoch")}
    assert boot_assert.schema_problems(rows, {"kernel": {"requires": 15, "code": 15}}) == []         # and the previous code's boot check accepts it
