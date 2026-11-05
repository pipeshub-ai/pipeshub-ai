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
    "kg.w_stamps_set": (([11], [3], ["rec"], [3], [b"\x00" * 8], [None], [False], [False], [False], [False], [None], [True]), "kg.records"),
}
CASTS = {   # the arguments psycopg cannot type from a list of Nones, or that are not what it sends
    "kg.w_stamps_set": ["%s::bigint[]", "%s::smallint[]", "%s::text[]", "%s::smallint[]", "%s::bytea[]", "%s::bigint[]", "%s::boolean[]", "%s::boolean[]", "%s::boolean[]", "%s::boolean[]",
                        "%s::smallint[]", "%s::boolean[]"],
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
    "kg.w_stamps_set": [([11, 12], [3], ["rec"], [3], [None], [None], [False], [False], [False], [False], [None], [True]), ([None], [3], ["rec"], [3], [None], [None], [False], [False], [False], [False], [None], [True]),
                        ([11], [4], ["rec"], [3], [None], [None], [False], [False], [False], [False], [None], [True]), ([11], [3], [""], [3], [None], [None], [False], [False], [False], [False], [None], [True]),
                        ([11], [3], ["rec"], [3], [None], [None], [False], [False], [False], [False], [9], [True]), ([11], [3], ["rec"], [3], [None], [None], [None], [False], [False], [False], [None], [True]),
                        (None, [3], ["rec"], [3], [None], [None], [False], [False], [False], [False], [None], [True])],
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


# ---- what is stored, not that a row exists --------------------------------------------------------------------------------------------------------------
REC_FIELDS = ("coll", "id", "kind", "app_sk", "group_sk", "parent_sk", "rule", "model", "vrid", "name", "name_key", "updated", "created", "size", "record_type", "origin",
              "external_id", "deleted", "placeholder", "granted")
REC_COLUMNS = "coll, id, kind, app_sk, group_sk, parent_sk, rule, model, vrid, name, name_key, updated_ms, created_ms, size_bytes, record_type, origin, external_id, deleted, placeholder, granted"


def rec_values(**over) -> dict:
    base = {"coll": 3, "id": "rec-1", "kind": 3, "app_sk": 5, "group_sk": 6, "parent_sk": 7, "rule": "RESTRICTED", "model": "RECORD_LEVEL", "vrid": "v1", "name": "Name",
            "name_key": "name", "updated": 20, "created": 10, "size": 100, "record_type": "FILE", "origin": "CONNECTOR", "external_id": "ext-1", "deleted": True,
            "placeholder": False, "granted": True}
    return {**base, **over}


def rec_args(*items: tuple[int, dict]) -> tuple:
    return ([sk for sk, _ in items], *([v[f] for _, v in items] for f in REC_FIELDS))


def stored_record(c, sk: int, org: str = ORG_A) -> tuple:
    return tuple(c.execute(f"SELECT {REC_COLUMNS} FROM kg.records WHERE org_id = %s AND sk = %s", (org, sk)).fetchone())


def test_a_record_is_stored_as_given_updated_in_place_and_a_repeat_changes_nothing(pdb):
    first, second = rec_values(), rec_values(name="Other", updated=30, deleted=False, placeholder=True, granted=False, rule="OPEN", app_sk=None)
    with conn(pdb) as c:
        clean(c)
        assert call(c, "kg.w_records_upsert", ORG_A, rec_args((11, first))) == [(1,)]
        assert stored_record(c, 11) == tuple(first[f] for f in REC_FIELDS)               # deleted and placeholder are not swapped, every column is the one given
        assert call(c, "kg.w_records_upsert", ORG_A, rec_args((11, first))) == [(1,)]
        assert stored_record(c, 11) == tuple(first[f] for f in REC_FIELDS)               # the same batch twice leaves the same row
        call(c, "kg.w_records_upsert", ORG_A, rec_args((11, second)))
        assert stored_record(c, 11) == tuple(second[f] for f in REC_FIELDS)              # a conflict updates the row in place
        assert c.execute("SELECT count(*) FROM kg.records WHERE org_id = %s", (ORG_A,)).fetchone()[0] == 1
        clean(c)


def test_a_key_named_twice_in_a_batch_keeps_its_last_item_in_every_writer(pdb):
    with conn(pdb) as c:
        clean(c)
        call(c, "kg.w_records_upsert", ORG_A, rec_args((11, rec_values(name="first")), (12, rec_values(id="rec-2")), (11, rec_values(name="last"))))
        assert stored_record(c, 11)[9] == "last" and c.execute("SELECT count(*) FROM kg.records WHERE org_id = %s", (ORG_A,)).fetchone()[0] == 2
        call(c, "kg.w_closure_set", ORG_A, ([1, 1], [2, 2], [0, 0], ["self", "org"]))
        assert c.execute("SELECT via FROM kg.principal_closure WHERE org_id = %s", (ORG_A,)).fetchall() == [("org",)]
        call(c, "kg.w_grants_set", ORG_A, ([1, 1], [2, 2], ["USER", "USER"], ["READER", "OWNER"], [5, 6], [False, True]))
        assert c.execute("SELECT role, app_sk, leaf FROM kg.scope_members WHERE org_id = %s", (ORG_A,)).fetchall() == [("OWNER", 6, True)]
        assert call(c, "kg.w_links_set", ORG_A, ([1, 1], [1, 1], [2, 2])) == [(1,)]
        assert call(c, "kg.w_app_cross_set", ORG_A, ([5, 5],)) == [(1,)]
        assert call(c, "kg.w_class_literals_set", ORG_A, ([5, 5], [7, 7])) == [(1,)]
        assert call(c, "kg.w_nodes_upsert", ORG_A, ([3, 3], ["dup", "dup"])) == [(3, "dup", c.execute("SELECT sk FROM kg.node_keys WHERE org_id = %s AND id = 'dup'", (ORG_A,)).fetchone()[0])]
        assert call(c, "kg.w_classes_set", ORG_A, ([7, 7], [1, 1], [None, None], ['[[1]]', '[[1]]'], [b"\x01" * 32, b"\x01" * 32])) == [(1,)]
        clean(c)


def test_each_set_writer_stores_the_values_it_is_given_and_updates_them_in_place(pdb):
    with conn(pdb) as c:
        clean(c)
        for _ in range(2):                                                                  # the same call twice is the same state
            call(c, "kg.w_grants_set", ORG_A, ([1], [2], ["USER"], ["READER"], [5], [True]))
            assert c.execute("SELECT principal_sk, node_sk, gtype, role, app_sk, leaf FROM kg.scope_members WHERE org_id = %s", (ORG_A,)).fetchall() == [(1, 2, "USER", "READER", 5, True)]
        call(c, "kg.w_grants_set", ORG_A, ([1], [2], ["USER"], [None], [6], [False]))
        assert c.execute("SELECT role, app_sk, leaf FROM kg.scope_members WHERE org_id = %s", (ORG_A,)).fetchall() == [(None, 6, False)]
        call(c, "kg.w_closure_set", ORG_A, ([1], [2], [0], ["self"]))
        call(c, "kg.w_closure_set", ORG_A, ([1], [2], [0], ["link"]))
        assert c.execute("SELECT user_sk, principal_sk, app_sk, via FROM kg.principal_closure WHERE org_id = %s", (ORG_A,)).fetchall() == [(1, 2, 0, "link")]
        call(c, "kg.w_links_set", ORG_A, ([1], [2], [3]))
        assert c.execute("SELECT parent_sk, kind, child_sk FROM kg.links WHERE org_id = %s", (ORG_A,)).fetchall() == [(1, 2, 3)]
        call(c, "kg.w_class_literals_set", ORG_A, ([5], [7]))
        assert c.execute("SELECT anchor_sk, class_id FROM kg.class_literals WHERE org_id = %s", (ORG_A,)).fetchall() == [(5, 7)]
        call(c, "kg.w_classes_set", ORG_A, ([7], [1], [2], ['[[1,"@self"]]'], [b"\x01" * 32]))
        assert c.execute("SELECT id, app_sk, group_sk, clauses, hash FROM kg.scope_classes WHERE org_id = %s", (ORG_A,)).fetchall() == [(7, 1, 2, [[1, "@self"]], b"\x01" * 32)]
        assert call(c, "kg.w_classes_set", ORG_A, ([7], [1], [2], ['[[1,"@self"]]'], [b"\x01" * 32])) == [(0,)]       # the same class again: nothing new
        clean(c)


def test_the_facts_of_a_connector_are_written_by_their_own_writer_and_leave_the_others_alone(pdb):
    with conn(pdb) as c:
        clean(c)
        cols = "app_sk, code_epoch, mode, verify_digest, backfill_cursor, opens, has_group_level, facts_set, cross_app"
        call(c, "kg.w_app_facts_set", ORG_A, (5, True, True))
        assert tuple(c.execute(f"SELECT {cols} FROM kg.projection_state WHERE org_id = %s", (ORG_A,)).fetchone()) == (5, 0, "legacy", None, None, True, True, True, False)
        call(c, "kg.w_app_cross_set", ORG_A, ([5],))
        assert tuple(c.execute(f"SELECT {cols} FROM kg.projection_state WHERE org_id = %s", (ORG_A,)).fetchone()) == (5, 0, "legacy", None, None, True, True, True, True)
        call(c, "kg.w_projection_set", ORG_A, (5, 3, "enforce", "digest", "cursor", None))
        assert tuple(c.execute(f"SELECT {cols} FROM kg.projection_state WHERE org_id = %s", (ORG_A,)).fetchone()) == (5, 3, "enforce", "digest", "cursor", True, True, True, True)
        call(c, "kg.w_app_facts_set", ORG_A, (5, False, False))
        assert tuple(c.execute(f"SELECT {cols} FROM kg.projection_state WHERE org_id = %s", (ORG_A,)).fetchone()) == (5, 3, "enforce", "digest", "cursor", False, False, True, True)
        clean(c)


@pytest.mark.parametrize("column, value", [("rule", "ADMIN"), ("model", "everything"), ("origin", "ELSEWHERE")])
def test_a_closed_set_refuses_a_value_outside_it_in_a_record(pdb, column, value):
    with conn(pdb) as c:
        clean(c)
        with pytest.raises(pg.InvalidParameterValue):
            call(c, "kg.w_records_upsert", ORG_A, rec_args((11, rec_values(**{column: value}))))
        clean(c)


def test_a_closure_via_outside_the_set_is_refused(pdb):
    with conn(pdb) as c:
        clean(c)
        with pytest.raises(pg.InvalidParameterValue):
            call(c, "kg.w_closure_set", ORG_A, ([1], [2], [0], ["friend"]))
        for via in ("self", "membership", "org", "link", "link_badge"):
            call(c, "kg.w_closure_set", ORG_A, ([1], [2], [0], [via]))
        clean(c)


def test_a_class_id_is_not_reused_for_other_content_and_its_clauses_are_an_array(pdb):
    with conn(pdb) as c:
        clean(c)
        call(c, "kg.w_classes_set", ORG_A, ([21], [1], [None], ["[[1]]"], [b"\x02" * 32]))
        for bad in (([21], [2], [None], ["[[2]]"], [b"\x03" * 32]),                                     # the id is stored with other content
                    ([22, 22], [1, 1], [None, None], ["[[1]]", "[[2]]"], [b"\x04" * 32, b"\x05" * 32]),   # named twice with different content
                    ([23], [1], [None], ["null"], [b"\x06" * 32])):                                    # a JSON null is not a list of clauses
            with pytest.raises(pg.InvalidParameterValue):
                call(c, "kg.w_classes_set", ORG_A, bad)
        assert c.execute("SELECT id, app_sk FROM kg.scope_classes WHERE org_id = %s", (ORG_A,)).fetchall() == [(21, 1)]
        clean(c)


# ---- locks are taken in key order -----------------------------------------------------------------------------------------------------------------------
CLASS_A = ([7], [1], [None], ['[[1,"@self"]]'], [b"\x01" * 32])


def _hammer(pdb, name: str, make, rounds: int) -> list[str]:
    """Two sessions of one org call ``name`` with the same keys, one ascending and one descending; the errors they met."""
    import threading
    errors: list[str] = []

    def run(descending: bool) -> None:
        with conn(pdb) as c:
            for i in range(rounds):
                keys = make(i)
                args = tuple(a[::-1] if descending else a for a in keys)
                try:
                    with unit(c, "pipeshub_connectors", ORG_A):
                        c.execute(f"SELECT * FROM {name}({marks(args, name)})", args).fetchall()
                except Exception as exc:
                    errors.append(type(exc).__name__)

    threads = [threading.Thread(target=run, args=(d,)) for d in (False, True)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


def test_a_class_committed_meanwhile_with_other_content_is_refused_not_taken_for_written(pdb):
    """The check before the insert cannot see a row another transaction has not committed yet; the insert waits for it, skips on the conflict, and the writer must not answer as if its class were stored."""
    import threading
    import time

    with conn(pdb) as c:
        clean(c)
    outcome: list[object] = []
    with conn(pdb) as holder, conn(pdb) as writer, conn(pdb) as watch:
        def run() -> None:
            try:
                outcome.append(call(writer, "kg.w_classes_set", ORG_A, ([7], [1], [None], ['[[1,"@self"]]'], [b"\x02" * 32])))
            except Exception as exc:
                outcome.append(exc)

        thread = threading.Thread(target=run)
        with unit(holder, "pipeshub_connectors", ORG_A):
            holder.execute(f"SELECT {'kg.w_classes_set'}({marks(CLASS_A, 'kg.w_classes_set')})", CLASS_A).fetchall()        # not committed yet
            thread.start()
            deadline = time.monotonic() + 60
            while not watch.execute(
                "SELECT 1 FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND pid NOT IN (pg_backend_pid(), %s) AND query LIKE '%%w_classes_set%%'",
                (holder.info.backend_pid,),
            ).fetchone():
                assert time.monotonic() < deadline, "the writer never reached the conflicting insert"
        thread.join()
        stored = holder.execute("SELECT hash FROM kg.scope_classes WHERE org_id = %s AND id = 7", (ORG_A,)).fetchall()
    assert len(outcome) == 1 and isinstance(outcome[0], pg.InvalidParameterValue), outcome
    assert stored == [(b"\x01" * 32,)]
    with conn(pdb) as c:
        clean(c)


@pytest.mark.parametrize("name, make", [
    ("kg.w_closure_set", lambda i: (list(range(1, 201)), list(range(1001, 1201)), [0] * 200, ["self"] * 200)),
    ("kg.w_grants_set", lambda i: (list(range(1, 201)), list(range(1001, 1201)), ["USER"] * 200, ["READER"] * 200, [5] * 200, [False] * 200)),
    ("kg.w_nodes_upsert", lambda i: ([3] * 200, [f"n{i}-{j:03}" for j in range(200)])),
])
def test_overlapping_batches_in_opposite_orders_do_not_deadlock(pdb, name, make):
    with conn(pdb) as c:
        clean(c)
        if name != "kg.w_nodes_upsert":
            call(c, name, ORG_A, make(0))                                                     # the rows exist, so a conflict locks them
    assert _hammer(pdb, name, make, 100) == []
    with conn(pdb) as c:
        clean(c)


def test_stamps_set_writes_the_stamp_columns_of_a_row_and_leaves_the_rest(pdb):
    with conn(pdb) as c:
        clean(c)
        call(c, "kg.w_records_upsert", ORG_A, rec_args((11, rec_values())))
        before = c.execute("SELECT " + REC_COLUMNS + " FROM kg.records WHERE org_id = %s AND sk = 11", (ORG_A,)).fetchone()
        assert call(c, "kg.w_stamps_set", ORG_A, ([11], [3], ["rec-1"], [3], [b"\x00\x01"], [None], [True], [True], [False], [True], [4], [False])) == [(1,)]
        got = c.execute("SELECT path, class_id, self_lit, seed, own_grant, under_deleted, legacy_reason, leaf, " + REC_COLUMNS + " FROM kg.records WHERE org_id = %s AND sk = 11", (ORG_A,)).fetchone()
        assert tuple(got[:8]) == (b"\x00\x01", None, True, True, False, True, 4, False)
        assert tuple(got[8:]) == tuple(before)                       # the structural columns are what the other writer left
        clean(c)
