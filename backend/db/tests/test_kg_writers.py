"""The vetted writers of schema kg (kg_0002): another org cannot be targeted, no org does nothing, bad arguments are 22023, only the connectors
service executes them, and the statements are the pinned shapes (the catalog is clean). The per-org row policy is in test_vetted_org_policy.py."""
# ruff: noqa: F811
import pytest
from psycopg import errors as pg

from db.lib import vetted

from .rls_fixture import ORG_A, ORG_B
from .test_vetted_pins import (  # noqa: F401  (pdb is the module fixture)
    FNS,
    conn,
    pdb,
    unit,
)

WRITERS = sorted(f.qualified for f in FNS.values() if f.schema == "kg")
R = ([11], [3], ["rec"], [3], [None], [None], [None], [None], [None], [None], ["name"], ["name"], [None], [None], [None], [None], [None], [None], [False], [False], [False])
CALLS = {   # name -> (arguments of one call, the table, a count query for one org)
    "kg.w_nodes_upsert": (([3], ["needle"]), "kg.node_keys"),
    "kg.w_records_upsert": (R, "kg.records"),
    "kg.w_records_delete": (([11],), "kg.records"),
    "kg.w_links_set": (([1], [1], [2]), "kg.links"),
    "kg.w_links_delete": (([1], [1], [2]), "kg.links"),
    "kg.w_grants_set": (([1], [2], ["USER"], ["READER"], [None], [False]), "kg.scope_members"),
    "kg.w_grants_delete": (([1], [2], ["USER"]), "kg.scope_members"),
    "kg.w_closure_set": (([1], [2], [0], ["self"]), "kg.principal_closure"),
    "kg.w_closure_delete": (([1], [2], [0]), "kg.principal_closure"),
    "kg.w_changes_ack": ((), "kg.graph_changes"),
    "kg.w_changes_delete": (([1],), "kg.graph_changes"),
    "kg.w_projection_set": ((5, 1, "shadow", "digest", "cursor", None), "kg.projection_state"),
    "kg.w_app_cross_set": (([5],), "kg.projection_state"),
    "kg.w_classes_set": (([7], [1], [None], ['[[1,"@self"]]'], [b"\x01" * 32]), "kg.scope_classes"),
    "kg.w_class_literals_set": (([5], [7]), "kg.class_literals"),
    "kg.w_app_facts_set": ((5, True, False), "kg.projection_state"),
}
CASTS = {   # the arguments psycopg cannot type from a list of Nones, or that are not what it sends
    "kg.w_classes_set": ["%s::bigint[]", "%s::bigint[]", "%s::bigint[]", "%s::text[]::jsonb[]", "%s::bytea[]"],
    "kg.w_grants_set": ["%s::bigint[]", "%s::bigint[]", "%s::text[]", "%s::text[]", "%s::bigint[]", "%s::boolean[]"],
}


def marks(args: tuple, name: str | None = None) -> str:
    return ", ".join(CASTS[name]) if name in CASTS and len(args) == len(CASTS[name]) else ", ".join("%s" for _ in args)


def call(c, name: str, org: str | None, args: tuple):
    sql = f"SELECT * FROM {name}({marks(args, name)})" if name == "kg.w_nodes_upsert" else f"SELECT {name}({marks(args, name)})"
    with unit(c, "pipeshub_connectors", org):
        return c.execute(sql, args).fetchall()


def rows(c, table: str, org: str) -> list:
    return c.execute(f"SELECT * FROM {table} WHERE org_id = %s ORDER BY 1, 2, 3", (org,)).fetchall()


def clean(c) -> None:
    for t in sorted({v[1] for v in CALLS.values()}):
        c.execute(f"DELETE FROM {t}")


def test_the_manifest_has_the_writers_and_the_catalog_is_clean(pdb):
    assert WRITERS == sorted(CALLS)
    with conn(pdb) as c:
        assert [tuple(r) for r in c.execute("SELECT rule, object FROM kernel.rls_violations")] == []
        assert vetted.problems(c, FNS) == []
        pins = {r[0] for r in c.execute("SELECT DISTINCT proc::regprocedure::text FROM kernel.vetted_write_pins WHERE rel::text LIKE 'kg.%'")}
        assert len(pins) == len(CALLS) and all(p.startswith("kg.w_") for p in pins)


def test_every_kg_table_requires_the_per_org_policy_and_is_plain_with_org_first_in_every_key(pdb):
    """A kg table added without ``kernel.require_vetted_org_policy`` fails here; a key without org_id first would block a later partition conversion."""
    with conn(pdb) as c:
        tables = c.execute("SELECT c.oid::regclass::text, c.relkind::text, c.relispartition, COALESCE(r.vetted_org_policy, false), r.class FROM pg_class c "
                           "LEFT JOIN kernel.rls_registry r ON r.rel = c.oid WHERE c.relnamespace = 'kg'::regnamespace AND c.relkind IN ('r', 'p')").fetchall()
        assert len(tables) == 9, tables
        assert [t[0] for t in tables if not t[3]] == [], "kg tables without kernel.require_vetted_org_policy"
        # rule 128 exempts a table the vetted role also reads unbound; no kg table is one (its vetted functions read through the per-org policy), and one that set the flag
        # would slip past 128 with the policy missing
        assert c.execute("SELECT r.rel::text FROM kernel.rls_registry r WHERE r.rel::text LIKE 'kg.%' AND r.vetted_read").fetchall() == []
        assert all(t[1] == "r" and not t[2] and t[4] == "org" for t in tables), tables
        keys = c.execute("SELECT i.indrelid::regclass::text, a.attname FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid "
                         "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = i.indkey[0] "
                         "WHERE c.relnamespace = 'kg'::regnamespace AND (i.indisprimary OR i.indisunique OR i.indexrelid::regclass::text LIKE 'kg.%')").fetchall()
        assert keys and [k for k in keys if k[1] != "org_id"] == [], keys


@pytest.mark.parametrize("name", [n for n in CALLS if not n.endswith(("_delete", "_ack", "_done", "_touch"))])
def test_a_set_writes_the_session_org_and_leaves_the_other_orgs_row_as_it_was(pdb, name):
    args, table = CALLS[name]
    with conn(pdb) as c:
        clean(c)
        call(c, name, ORG_B, args)
        before = rows(c, table, ORG_B)
        assert before
        call(c, name, ORG_A, args)
        assert rows(c, table, ORG_B) == before
        assert rows(c, table, ORG_A)
        assert {r[0] for r in c.execute(f"SELECT org_id FROM {table}")} == {ORG_A, ORG_B}
        clean(c)


@pytest.mark.parametrize("name, seed", [("kg.w_records_delete", "kg.w_records_upsert"), ("kg.w_links_delete", "kg.w_links_set"), ("kg.w_grants_delete", "kg.w_grants_set"),
                                        ("kg.w_closure_delete", "kg.w_closure_set")])
def test_a_delete_with_the_keys_of_another_orgs_rows_deletes_nothing_of_theirs(pdb, name, seed):
    args, table = CALLS[name]
    with conn(pdb) as c:
        clean(c)
        call(c, seed, ORG_B, CALLS[seed][0])
        call(c, seed, ORG_A, CALLS[seed][0])
        assert call(c, name, ORG_A, args) == [(1,)]                                                 # its own row
        assert call(c, name, ORG_A, args) == [(0,)]
        assert len(rows(c, table, ORG_B)) >= 1
        assert call(c, name, ORG_B, args) == [(1,)]
        clean(c)


def test_the_change_log_is_acknowledged_by_transaction_and_by_id_and_only_in_the_session_org(pdb):
    with conn(pdb) as c:
        clean(c)
        for org in (ORG_A, ORG_B):
            c.execute("INSERT INTO kg.graph_changes (org_id, txid, ends) VALUES (%s, '100'::xid8, ARRAY['3:needle']), (%s, '101'::xid8, ARRAY['3:other', '4:u'])", (org, org))
        ids = {org: [r[0] for r in c.execute("SELECT id FROM kg.graph_changes WHERE org_id = %s ORDER BY id", (org,))] for org in (ORG_A, ORG_B)}
        assert call(c, "kg.w_changes_delete", ORG_A, ([ids[ORG_A][0], ids[ORG_B][0]],)) == [(1,)]                    # an id of the other org is not theirs to delete
        assert call(c, "kg.w_changes_delete", ORG_A, ([ids[ORG_A][0]],)) == [(0,)]
        assert len(rows(c, "kg.graph_changes", ORG_B)) == 2 and len(rows(c, "kg.graph_changes", ORG_A)) == 1
        assert call(c, "kg.w_changes_ack", ORG_A, ()) == [(0,)]                                       # this transaction wrote none
        c.execute("BEGIN")
        c.execute("INSERT INTO kg.graph_changes (org_id, ends) VALUES (%s, ARRAY['3:mine'])", (ORG_A,))              # as the capture trigger will: this transaction's id by default
        c.execute("SET LOCAL ROLE pipeshub_connectors")
        c.execute("SELECT set_config('app.org_id', %s, true)", (ORG_A,))
        assert c.execute("SELECT kg.w_changes_ack()").fetchone()[0] == 1
        c.execute("COMMIT")
        assert [list(r[4]) for r in rows(c, "kg.graph_changes", ORG_A)] == [["3:other", "4:u"]]
        clean(c)


@pytest.mark.parametrize("name", sorted(CALLS))
def test_no_org_context_does_nothing(pdb, name):
    args, table = CALLS[name]
    with conn(pdb) as c:
        clean(c)
        before = c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for org in (None, ""):
            got = call(c, name, org, args)
            assert got in ([(0,)], [], [(False,)]), got
        assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == before


BAD = {
    "kg.w_nodes_upsert": [([0], ["x"]), ([3], [""]), ([3, 3], ["x"]), ([None], ["x"]), ([3], [None]), (None, ["x"]), ([3] * 5001, ["x"] * 5001)],
    "kg.w_records_upsert": [([11, 12], *list(R[1:])), ([None], *list(R[1:])), (*list(R[:3]), [9], *list(R[4:])), (*list(R[:2]), [""], *list(R[3:])), (None, *list(R[1:]))],
    "kg.w_records_delete": [([None],), (None,), ([1] * 5001,)],
    "kg.w_links_set": [([1], [3], [2]), ([1, 2], [1], [2]), ([None], [1], [2])],
    "kg.w_links_delete": [([1], [3], [2]), ([1, 2], [1], [2])],
    "kg.w_grants_set": [([1], [2], [""], [None], [None], [False]), ([1], [2], ["USER"], ["x" * 65], [None], [False]), ([1, 2], [2], ["USER"], [None], [None], [False]),
                        ([1], [2], ["USER"], [None], [None], [None])],
    "kg.w_grants_delete": [([1], [2], [""]), ([1], [2, 3], ["USER"])],
    "kg.w_closure_set": [([1], [2], [0], [""]), ([1], [2], [None], ["self"])],
    "kg.w_closure_delete": [([1], [2], [None]), ([1], [2], [0, 1])],
    "kg.w_changes_delete": [([None],), (None,), ([1] * 5001,)],
    "kg.w_app_cross_set": [([None],), (None,), ([-1],), ([1] * 5001,)],
    "kg.w_classes_set": [([7], [1], [None], [None], [b"x"]), ([7, 8], [1], [None], ['[]'], [b"x"])],
    "kg.w_class_literals_set": [([5], [7, 8]), ([None], [7])],
    "kg.w_app_facts_set": [(-1, True, False), (None, True, False), (5, None, False)],
    "kg.w_projection_set": [(-1, 1, "shadow", None, None, None), (5, 1, "bogus", None, None, None), (5, -1, "shadow", None, None, None), (5, 1, "shadow", "x" * 129, None, None), (None, 1, "shadow", None, None, None)],
}


@pytest.mark.parametrize("name, bad", [(n, b) for n, bs in sorted(BAD.items()) for b in bs], ids=lambda v: v if isinstance(v, str) else "args")
def test_bad_arguments_are_22023(pdb, name, bad):
    with conn(pdb) as c:
        clean(c)
        with pytest.raises(pg.InvalidParameterValue):
            call(c, name, ORG_A, bad)
        assert all(c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] == 0 for t in sorted({v[1] for v in CALLS.values()}))


@pytest.mark.parametrize("name", sorted(CALLS))
@pytest.mark.parametrize("role", ["pipeshub_node", "pipeshub_indexing", "pipeshub_query", "pipeshub_jobs", "pipeshub_backup", "pipeshub_restore", "pipeshub_ro"])
def test_only_the_connectors_service_executes_a_writer(pdb, name, role):
    args, _ = CALLS[name]
    with conn(pdb) as c, unit(c, role, ORG_A), pytest.raises(pg.InsufficientPrivilege):
        c.execute(f"SELECT {name}({marks(args, name)})", args)


def test_text_in_arguments_is_data(pdb):
    with conn(pdb) as c:
        clean(c)
        evil = "x'); DROP TABLE kg.records; --"
        call(c, "kg.w_nodes_upsert", ORG_A, ([3], [evil]))
        assert c.execute("SELECT id FROM kg.node_keys WHERE org_id = %s", (ORG_A,)).fetchall() == [(evil,)]
        assert c.execute("SELECT to_regclass('kg.records') IS NOT NULL").fetchone()[0]
        clean(c)
