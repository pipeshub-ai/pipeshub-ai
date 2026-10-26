"""The extension point of kernel.org_lineage, and built-in function definitions in the security snapshot.
These use a database per test (the `db` fixture drops every pipeshub role), so they live apart from test_vetted.py."""
import textwrap
from pathlib import Path

import pytest
import yaml
from psycopg import errors as pg

from db import surface
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


def extension_dir(tmp_path: Path, body: str, sql: str = "") -> dict:
    d = tmp_path / "ext"
    (d / "vetted").mkdir(parents=True)
    (d / "vetted" / "manifest.yaml").write_text(yaml.safe_dump({"functions": [
        {"name": "kernel.org_parent_ids", "class": "read", "scope": "helper", "extends": True, "returns": "text[]",
         "args": [{"name": "p_org", "type": "text", "pattern": "^[0-9a-f]{24}$"}], "body": body}]}))
    for name, generated in vetted.render_all(vetted.load(d / "vetted" / "manifest.yaml")).items():
        (d / "vetted" / name).write_text(generated)
    (d / "0001_parents.sql").write_text(sql or "SELECT 1;")
    (d / "0001_parents.py").write_text(textwrap.dedent('''
        from db.lib.run_sql import run_sql
        from db.lib.vetted import run_vetted
        revision = "ext_0001"
        down_revision = None
        branch_labels = ("ext",)
        depends_on = "kernel_0010"
        def upgrade():
            run_sql(__file__)
            run_vetted(__file__, "org_parent_ids")
        def downgrade():
            pass
    '''))
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


