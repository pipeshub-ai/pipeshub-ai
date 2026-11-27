"""The extension point of kernel.org_lineage, and built-in function definitions in the security snapshot.
These use a database per test (the `db` fixture drops every pipeshub role), so they live apart from test_vetted.py."""
import textwrap
from pathlib import Path

import pytest
import yaml
from psycopg import errors as pg

from db import bootstrap_roles, surface
from db.lib import vetted

from .conftest import run_migrate
from .rls_fixture import ORG_A, ORG_B, ORG_P

LINKS_SQL = f"""
CREATE SCHEMA org_hierarchy;
SELECT kernel.declare_extension_schema('org_hierarchy');
CREATE TABLE org_hierarchy.org_links (org_id text NOT NULL, parent_org_id text NOT NULL, PRIMARY KEY (org_id, parent_org_id));
SELECT kernel.apply_rls('org_hierarchy.org_links', 'global');
SELECT kernel.grant_vetted_owner_select('org_hierarchy.org_links');
INSERT INTO org_hierarchy.org_links VALUES ('{ORG_A}', '{ORG_P}'), ('{ORG_A}', 'x,y'), ('{ORG_A}', 'ZZ'), ('{ORG_A}', '{ORG_A}'), ('{ORG_A}', '');
"""
LINKS_BODY = "RETURN ARRAY(SELECT l.parent_org_id FROM org_hierarchy.org_links l WHERE l.org_id = p_org);"


def extension_dir(tmp_path: Path, body: str, sql: str = "", extra: list | None = None, runs: tuple = ("org_parent_ids",),
                  after: str = "kernel_0010") -> dict:
    d = tmp_path / "ext"
    (d / "vetted").mkdir(parents=True)
    (d / "vetted" / "manifest.yaml").write_text(yaml.safe_dump({"functions": [
        {"name": "kernel.org_parent_ids", "class": "read", "scope": "helper", "extends": True, "returns": "text[]",
         "args": [{"name": "p_org", "type": "text", "pattern": "^[0-9a-f]{24}$"}], "body": body}, *(extra or [])]}))
    for name, generated in vetted.render_all(vetted.load(d / "vetted" / "manifest.yaml")).items():
        (d / "vetted" / name).write_text(generated)
    (d / "0001_parents.sql").write_text(sql or "SELECT 1;")
    (d / "0001_parents.py").write_text(textwrap.dedent('''
        from db.lib.run_sql import run_sql
        from db.lib.vetted import run_vetted
        revision = "ext_0001"
        down_revision = None
        branch_labels = ("ext",)
        depends_on = "AFTER"
        def upgrade():
            run_sql(__file__)
            run_vetted(__file__, *RUNS)
        def downgrade():
            pass
    ''').replace("RUNS", repr(tuple(runs))).replace("AFTER", after))
    ed = tmp_path / "edition.py"
    ed.write_text(f"EXTRA_MIGRATION_LOCATIONS = {[str(d)]!r}\n")
    return {"PIPESHUB_EDITION_FILE": str(ed)}


def test_an_extra_migration_location_replaces_the_parent_lookup_and_adds_parents(db, tmp_path):
    cluster, name = db
    url = cluster.url(name, "pipeshub_migrate")
    assert run_migrate(url).returncode == 0
    with cluster.admin(name) as c:
        old = [x for x in surface.live_lines(c) if x.startswith("func kernel.org_parent_ids")]
    assert len(old) == 1

    # a body changed in place, by anyone, is refused by the security surface
    with cluster.admin(name) as c:
        try:
            c.execute("BEGIN")
            c.execute("CREATE OR REPLACE FUNCTION kernel.org_parent_ids(p_org text) RETURNS text[] LANGUAGE plpgsql STABLE SECURITY DEFINER "
                      "SET search_path = pg_catalog, kernel, pg_temp SET standard_conforming_strings = on AS $$ BEGIN RETURN ARRAY['" + ORG_B + "']; END $$")
            assert any("org_parent_ids" in line for _, line in surface.violations(c))
        finally:
            c.execute("ROLLBACK")

    # the location reads its own table: the replacement body, the table, the vetted owner's SELECT on it (and only that)
    env = extension_dir(tmp_path, LINKS_BODY, LINKS_SQL)
    r = run_migrate(url, **env)                                    # the location is not listed: refused
    assert r.returncode == 4 and "org_parent_ids" in r.stdout, r.stdout
    pairs = [ln for ln in r.stdout.splitlines() if "50 surface:" in ln]
    unexpected = [ln.split(", '", 1)[1].rstrip(")'") for ln in pairs if "50 surface: unexpected" in ln]
    missing = [ln.split(", '", 1)[1].rstrip(")'") for ln in pairs if "50 surface: missing" in ln]
    grant = "relacl org_hierarchy.org_links pipeshub_vetted_kernel SELECT grantable=false"
    assert grant in unexpected, unexpected                         # the snapshot records the grant
    assert missing == old, missing                                 # the old body is the only line that went away
    assert not any("INSERT" in x or "UPDATE" in x for x in unexpected if x.startswith("relacl"))
    allow = Path(env["PIPESHUB_EDITION_FILE"]).parent / "ext" / "allowlist.yaml"
    quoted = "".join(f'  - "{x}"\n' for x in unexpected)
    allow.write_text(f"surface:\n{quoted}")                       # the old line is still expected
    assert run_migrate(url, **env).returncode == 4
    allow.write_text("surface:\n" + "".join(f'  - "{x}"\n' for x in unexpected if x != grant) + f'remove:\n  - "{old[0]}"\n')
    assert run_migrate(url, **env).returncode == 4                 # without the grant line: refused
    allow.write_text(f"surface:\n{quoted}remove:\n" + f'  - "{old[0]}"\n')
    r = run_migrate(url, **env)
    assert r.returncode == 0, r.stdout
    with cluster.admin(name) as c, c.transaction(force_rollback=True):      # only SELECT is waived: anything more is still a violation
        c.execute("GRANT INSERT ON org_hierarchy.org_links TO pipeshub_vetted_kernel")
        assert any(r.startswith(("73 ", "42 ")) for r, _ in c.execute("SELECT rule, object FROM kernel.rls_violations"))
    with cluster.admin(name) as c:
        assert c.execute("SELECT rule, object FROM kernel.rls_violations").fetchall() == []
    refused = {                                                    # the grant is limited to schemas this location declared, and to new ones
        "a core table": "SELECT kernel.grant_vetted_owner_select('kernel.settings')",
        "a schema that is not declared": "CREATE SCHEMA other_ext; CREATE TABLE other_ext.t (a int); SELECT kernel.grant_vetted_owner_select('other_ext.t')",
        "declaring a schema that has tables": "CREATE SCHEMA late_ext; CREATE TABLE late_ext.t (a int); SELECT kernel.declare_extension_schema('late_ext')",
        "declaring the kernel schema": "SELECT kernel.declare_extension_schema('kernel')",
    }
    for sql in refused.values():
        with cluster.admin(name) as c, c.transaction(force_rollback=True):
            c.execute("SET LOCAL ROLE pipeshub_owner")
            with pytest.raises(pg.RaiseException):
                with c.transaction():
                    c.execute(sql)

    with cluster.admin(name) as c:                                # what the unit of work gets
        def lineage(as_org, asked):
            with c.transaction(force_rollback=True):
                c.execute("SET LOCAL ROLE pipeshub_node")
                c.execute("SELECT set_config('app.org_id', %s, true)", (as_org or "",))
                return c.execute("SELECT kernel.org_lineage(%s)", (asked,)).fetchone()[0]
        assert lineage(ORG_A, ORG_A) == [ORG_A, ORG_P]            # the org first, then its parents: sorted, de-duplicated, well-formed only
        assert ",".join(lineage(ORG_A, ORG_A)) == f"{ORG_A},{ORG_P}"      # the list the lineage policy splits on commas
        assert lineage(ORG_B, ORG_B) == [ORG_B]
        assert lineage(ORG_A, ORG_B) == []                       # asking for another org's lineage answers like no org, parents stay private
        assert lineage(None, ORG_A) == []
        with pytest.raises(pg.InsufficientPrivilege):
            with c.transaction(force_rollback=True):
                c.execute("SET LOCAL ROLE pipeshub_node")
                c.execute("SELECT kernel.org_parent_ids(%s)", (ORG_A,))
        assert c.execute("SELECT rule, object FROM kernel.rls_violations").fetchall() == []


def test_an_extension_that_cannot_reach_the_parent_table_cannot_widen_what_it_returns(db, tmp_path):
    """A replacement body that returns junk (commas, upper case, 40 parents) is filtered by org_lineage itself."""
    cluster, name = db
    url = cluster.url(name, "pipeshub_migrate")
    parents = ", ".join(f"'{i:024x}'" for i in range(40))
    env = extension_dir(tmp_path, f"RETURN ARRAY[{parents}, 'a,b', 'A'];")
    r = run_migrate(url, **env, PG_MIGRATE_SURFACE_CHECK="skip")
    assert r.returncode == 0, r.stdout
    with cluster.admin(name) as c, c.transaction(force_rollback=True):
        c.execute("SET LOCAL ROLE pipeshub_node")
        c.execute("SELECT set_config('app.org_id', %s, true)", (ORG_A,))
        got = c.execute("SELECT kernel.org_lineage(%s)", (ORG_A,)).fetchone()[0]
    assert got[0] == ORG_A and len(got) == 17 and got[1:] == sorted(got[1:]) and all(len(x) == 24 and "," not in x for x in got)


# ---- an extension schema with its own vetted owner (the writers of the parent lookup's data) ---------------------------------

WRITERS_SQL = """
CREATE SCHEMA org_hierarchy;
SELECT kernel.declare_extension_schema('org_hierarchy');
CREATE TABLE org_hierarchy.org_links (org_id text PRIMARY KEY, parent_org_id text NOT NULL);
SELECT kernel.apply_rls('org_hierarchy.org_links', 'global', p_vetted => true, p_vetted_write => true);
SELECT kernel.grant_vetted_owner_select('org_hierarchy.org_links');
CREATE TABLE org_hierarchy.offers (parent_id text NOT NULL, child_id text NOT NULL, PRIMARY KEY (child_id, parent_id));
SELECT kernel.apply_rls('org_hierarchy.offers', 'global', p_vetted => true, p_vetted_write => true);
SELECT kernel.grant_job_gate('org_hierarchy');
GRANT USAGE ON SCHEMA org_hierarchy TO pipeshub_node, pipeshub_jobs;
"""
_ORG = {"name": "p", "type": "text", "pattern": "^[0-9a-f]{24}$"}
WRITERS = [
    {"name": "org_hierarchy.offer_child", "class": "write", "scope": "org", "writer": "pipeshub_node", "returns": "boolean", "no_org": "false",
     "args": [{**_ORG, "name": "p_child"}], "reads": ["org_hierarchy.offers"], "test": {"checks": ["privileges", "catalog"]},
     "body": "INSERT INTO org_hierarchy.offers (parent_id, child_id) SELECT v_org, p_child WHERE p_child <> v_org\n"
             "  AND NOT EXISTS (SELECT 1 FROM org_hierarchy.offers o WHERE o.parent_id = v_org AND o.child_id = p_child);\nRETURN true;"},
    {"name": "org_hierarchy.accept_parent", "class": "write", "scope": "org", "writer": "pipeshub_node", "returns": "boolean", "no_org": "false",
     "args": [{**_ORG, "name": "p_parent"}], "reads": ["org_hierarchy.org_links", "org_hierarchy.offers"], "test": {"checks": ["privileges", "catalog"]},
     "body": "IF EXISTS (SELECT 1 FROM org_hierarchy.org_links l WHERE l.org_id = v_org) THEN RETURN false; END IF;\n"
             "INSERT INTO org_hierarchy.org_links (org_id, parent_org_id) SELECT v_org, o.parent_id FROM org_hierarchy.offers o\n"
             "  WHERE o.child_id = v_org AND o.parent_id = p_parent;\nRETURN FOUND;"},
    {"name": "org_hierarchy.import_links", "class": "jobs", "scope": "global", "requires_job": ["parents_import"], "returns": "void",
     "args": [{"name": "p_org_ids", "type": "text[]", "max_items": 100, "item_not_null": True, "item_length": [24, 24]},
              {"name": "p_parent_ids", "type": "text[]", "max_items": 100, "item_not_null": True, "item_length": [24, 24]}],
     "reads": ["org_hierarchy.org_links"], "writes": ["org_hierarchy.org_links"], "test": {"checks": ["privileges", "catalog"]},
     "body": "INSERT INTO org_hierarchy.org_links (org_id, parent_org_id) SELECT k.o, k.p FROM unnest(p_org_ids, p_parent_ids) AS k(o, p)\n"
             "  WHERE NOT EXISTS (SELECT 1 FROM org_hierarchy.org_links l WHERE l.org_id = k.o);\nRETURN;"},
]


def test_an_extension_schema_gets_its_own_vetted_owner_whose_functions_write_only_it(db, tmp_path, monkeypatch):
    cluster, name = db
    url = cluster.url(name, "pipeshub_migrate")
    env = extension_dir(tmp_path, LINKS_BODY, WRITERS_SQL, extra=WRITERS, runs=("org_parent_ids", "offer_child", "accept_parent", "import_links"),
                        after="kernel_0023")
    monkeypatch.setenv("PIPESHUB_EDITION_FILE", env["PIPESHUB_EDITION_FILE"])
    with cluster.admin(name) as c:                                 # the one path: the bootstrap creates the owner the manifest needs
        assert bootstrap_roles.bootstrap(c, []) == ["pipeshub_vetted_org_hierarchy"]
    r = run_migrate(url, **env, PG_MIGRATE_SURFACE_CHECK="skip")
    assert r.returncode == 0, r.stdout                             # and the schema is still declared (before: "is not new")
    with cluster.admin(name) as c:
        assert c.execute("SELECT rule, object FROM kernel.rls_violations").fetchall() == []

        def call(role, org, sql, *args):
            with c.transaction(force_rollback=False):
                c.execute(f"SET LOCAL ROLE {role}")
                c.execute("SELECT set_config('app.org_id', %s, true)", (org or "",))
                return c.execute(sql, args).fetchall()
        lineage = lambda org: call("pipeshub_node", org, "SELECT kernel.org_lineage(%s)", org)[0][0]    # noqa: E731
        assert call("pipeshub_node", ORG_P, "SELECT org_hierarchy.offer_child(%s)", ORG_A) == [(True,)]
        assert lineage(ORG_A) == [ORG_A]                            # an offer alone links nothing
        assert call("pipeshub_node", ORG_B, "SELECT org_hierarchy.accept_parent(%s)", ORG_P) == [(False,)]   # not offered to B
        assert call("pipeshub_node", ORG_A, "SELECT org_hierarchy.accept_parent(%s)", ORG_P) == [(True,)]
        assert lineage(ORG_A) == [ORG_A, ORG_P]
        assert call("pipeshub_node", ORG_A, "SELECT org_hierarchy.accept_parent(%s)", ORG_P) == [(False,)]   # a child keeps its parent

        with pytest.raises(pg.ObjectNotInPrerequisiteState):          # the import runs only while its job runs
            call("pipeshub_jobs", None, "SELECT org_hierarchy.import_links(%s, %s)", [ORG_B], [ORG_P])
        c.execute("INSERT INTO platform.jobs (kind, status) VALUES ('parents_import', 'running')")
        call("pipeshub_jobs", None, "SELECT org_hierarchy.import_links(%s, %s)", [ORG_B], [ORG_P])
        assert lineage(ORG_B) == [ORG_B, ORG_P]

        for role, sql in (("pipeshub_vetted_kernel", f"INSERT INTO org_hierarchy.org_links VALUES ('{'d' * 24}', '{ORG_P}')"),   # kernel owner: SELECT only
                          ("pipeshub_vetted_org_hierarchy", "UPDATE kernel.settings SET owner = owner"),                       # its own schema only
                          ("pipeshub_vetted_org_hierarchy", "SELECT 1 FROM platform.kv_entries LIMIT 1"),
                          ("pipeshub_node", f"INSERT INTO org_hierarchy.org_links VALUES ('{'d' * 24}', '{ORG_P}')")):
            with pytest.raises(pg.InsufficientPrivilege), c.transaction(force_rollback=True):
                c.execute(f"SET LOCAL ROLE {role}")
                c.execute(sql)
        with c.transaction(force_rollback=True):                     # a write grant outside its schema is a catalog violation
            c.execute("GRANT INSERT ON platform.kv_entries TO pipeshub_vetted_org_hierarchy")
            assert any(rule.startswith("42 ") for rule, _ in c.execute("SELECT rule, object FROM kernel.rls_violations"))

    refused = {                                                       # a schema or owner that did not come by the one path is still refused
        "an owner that already holds a privilege": "CREATE SCHEMA late_a AUTHORIZATION pipeshub_owner; CREATE ROLE pipeshub_vetted_late_a NOLOGIN; "
                                                   "GRANT SELECT ON kernel.settings TO pipeshub_vetted_late_a; SET LOCAL ROLE pipeshub_owner; "
                                                   "SELECT kernel.declare_extension_schema('late_a')",
        "an owner that is a member of another role": "CREATE SCHEMA late_b AUTHORIZATION pipeshub_owner; CREATE ROLE pipeshub_vetted_late_b NOLOGIN IN ROLE pipeshub_app; "
                                                     "SET LOCAL ROLE pipeshub_owner; SELECT kernel.declare_extension_schema('late_b')",
        "an owner that can log in": "CREATE SCHEMA late_c AUTHORIZATION pipeshub_owner; CREATE ROLE pipeshub_vetted_late_c LOGIN; "
                                    "SET LOCAL ROLE pipeshub_owner; SELECT kernel.declare_extension_schema('late_c')",
        "a schema the owner role does not own": "CREATE SCHEMA late_d; SET LOCAL ROLE pipeshub_owner; SELECT kernel.declare_extension_schema('late_d')",
        "a core schema": "SET LOCAL ROLE pipeshub_owner; SELECT kernel.declare_extension_schema('identity')",
    }
    for why, sql in refused.items():
        with cluster.admin(name) as c, c.transaction(force_rollback=True):
            with pytest.raises(pg.RaiseException, match="is not new|is not available"):
                c.execute(sql)
            assert why


# ---- built-in function definitions are part of the snapshot ----------------------------------------------------------

# A changed body (prosrc) or library path (probin) is not here on purpose: it differs between builds and needs a superuser.
BUILTIN_PLANTS = [
    ("a built-in becomes SECURITY DEFINER", "UPDATE pg_proc SET prosecdef = true WHERE proname = 'texteq'"),
    ("a built-in gets a search_path setting", "UPDATE pg_proc SET proconfig = ARRAY['search_path=public'] WHERE proname = 'textne'"),
    ("a built-in is redefined in SQL", "UPDATE pg_proc SET prolang = (SELECT oid FROM pg_language WHERE lanname = 'sql'), prosrc = 'select true', probin = NULL WHERE proname = 'texteq'"),
    ("a built-in becomes leakproof", "UPDATE pg_proc SET proleakproof = true WHERE proname = 'textlike'"),
    ("a built-in changes its volatility", "UPDATE pg_proc SET provolatile = 'v' WHERE proname = 'lower'"),
    ("a built-in changes owner", "CREATE ROLE evil_owner; UPDATE pg_proc SET proowner = (SELECT oid FROM pg_roles WHERE rolname = 'evil_owner') WHERE proname = 'upper'"),
]


@pytest.mark.parametrize("plant_", BUILTIN_PLANTS, ids=[p[0] for p in BUILTIN_PLANTS])
def test_a_changed_built_in_function_is_seen_by_the_snapshot(db, plant_):
    cluster, name = db
    assert run_migrate(cluster.url(name, "pipeshub_migrate")).returncode == 0
    with cluster.admin(name) as c:
        assert surface.violations(c) == []
        try:
            c.execute("BEGIN")
            c.execute(plant_[1])
            found = surface.violations(c)
        finally:
            c.execute("ROLLBACK")
    assert any(line.startswith(("sysfuncs@", "sysfunc ")) for _, line in found), found


def test_a_secdef_built_in_is_also_a_catalog_violation(db):
    cluster, name = db
    assert run_migrate(cluster.url(name, "pipeshub_migrate")).returncode == 0
    with cluster.admin(name) as c:
        try:
            c.execute("BEGIN")
            c.execute("UPDATE pg_proc SET prosecdef = true WHERE proname = 'texteq'")
            assert any(r.startswith("70 ") for r, _ in c.execute("SELECT rule, object FROM kernel.rls_violations"))
        finally:
            c.execute("ROLLBACK")




def test_a_carriage_return_ends_a_line_comment_for_the_sql_helper_and_its_python_mirror(db):
    cluster, name = db
    assert run_migrate(cluster.url(name, "pipeshub_migrate")).returncode == 0
    for text, want in (("a -- c" + "\r" + "DELETE FROM t;", "a DELETE FROM t;"), ("a -- c" + "\n" + "DELETE FROM t;", "a DELETE FROM t;"),
                       ("a -- c DELETE FROM t;", "a"), ("a 'x -- y' b", "a '' b")):
        with cluster.admin(name) as c:
            assert c.execute("SELECT kernel.src_text(%s)", (text,)).fetchone()[0].strip() == want
        assert vetted.normalise(text).strip() == want
