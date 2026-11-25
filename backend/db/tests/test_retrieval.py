"""The retrieval module: tables, the default tree, row-level security on points, the jobs-only catalog writes, the lexical search function
and the space checks. The generic checks of the vetted functions are in test_vetted.py (its manifest list includes this module's manifest)."""


import psycopg
import pytest
from psycopg import errors as pg

from db import vetted_harness
from db.lib import fold

from .conftest import run_migrate, start_migrate
from .rls_fixture import ORG_A, ORG_B, uow

BIGGER = ("points", "point_text", "point_groups", "group_members", "point_tombstones", "postings", "lexical_df_delta")
VEC = "[0.5,0.25,-0.125,1]"


def connect_as(cluster, name, user: str) -> psycopg.Connection:
    return psycopg.connect(host="127.0.0.1", port=cluster.port, user=user, dbname=name, autocommit=True)


def adopt(cluster, name: str, org: str, dim: int = 4) -> None:
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", org):
        c.execute("SELECT active_id FROM retrieval.org_space_adopt('bge', %s, 'cosine')", (dim,))


def put(c: psycopg.Connection, org: str, point_id: str, space: int = 1, coll: int = 1) -> None:
    c.execute("INSERT INTO retrieval.points (org_id, collection_id, space_id, point_id, group_key, embedding, embedding_dim) "
              "VALUES (%s, %s, %s, %s, %s, %s::ext.halfvec, 4)", (org, coll, space, point_id, point_id, VEC))


def test_shape_of_the_big_tables(rdb):
    cluster, name = rdb
    with cluster.admin(name) as c:
        for t in BIGGER:
            assert c.execute("SELECT relkind FROM pg_class WHERE oid = %s::regclass", (f"retrieval.{t}",)).fetchone()[0] == "p"
            assert c.execute("SELECT relkind FROM pg_class WHERE oid = %s::regclass", (f"retrieval.{t}_default",)).fetchone()[0] == "p"
            assert c.execute("SELECT count(*) FROM pg_partition_tree(%s::regclass) WHERE isleaf", (f"retrieval.{t}",)).fetchone()[0] == 8
        # the vector columns: halfvec kept inline, the bit column is computed from it
        assert c.execute("SELECT attgenerated FROM pg_attribute WHERE attrelid = 'retrieval.points'::regclass AND attname = 'bq'").fetchone()[0] == "s"
        assert c.execute("SELECT attstorage FROM pg_attribute WHERE attrelid = 'retrieval.points'::regclass AND attname = 'embedding'").fetchone()[0] == "p"
        assert c.execute("SELECT count(*) FROM kernel.rls_violations").fetchone()[0] == 0


def test_the_default_tree_isolates_orgs(rdb):
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    adopt(cluster, name, ORG_B)
    with cluster.admin(name) as admin:
        admin.execute("SELECT * FROM retrieval.collection_list(0, 5)")      # callable by a superuser; the catalog is seeded
        assert admin.execute("SELECT name FROM retrieval.collections ORDER BY collection_id").fetchall() == [("records",), ("entities",)]
    with connect_as(cluster, name, "pipeshub_indexing_login") as c:
        with uow(c, "pipeshub_indexing", ORG_A):
            put(c, ORG_A, "00000000-0000-0000-0000-00000000000a")
        with uow(c, "pipeshub_indexing", ORG_A), pytest.raises(pg.InsufficientPrivilege):      # another org's row is refused by the policy
            put(c, ORG_B, "00000000-0000-0000-0000-00000000000b")
    with connect_as(cluster, name, "pipeshub_indexing_login") as c:
        with uow(c, "pipeshub_indexing", ORG_B):
            put(c, ORG_B, "00000000-0000-0000-0000-00000000000b")
    with connect_as(cluster, name, "pipeshub_query_login") as c:
        for org, other in ((ORG_A, ORG_B), (ORG_B, ORG_A)):
            with uow(c, "pipeshub_query", org):
                assert c.execute("SELECT org_id FROM retrieval.points").fetchall() == [(org,)]
                assert c.execute("SELECT count(*) FROM retrieval.points WHERE org_id = %s", (other,)).fetchone()[0] == 0
        with uow(c, "pipeshub_query", ORG_A), pytest.raises(pg.InsufficientPrivilege):    # the query service reads
            put(c, ORG_A, "00000000-0000-0000-0000-00000000000c")
        with uow(c, "pipeshub_query", None):
            assert c.execute("SELECT count(*) FROM retrieval.points").fetchone()[0] == 0   # no org: no rows


def test_the_catalog_is_written_by_the_jobs_role_only(rdb):
    cluster, name = rdb
    create = "SELECT new_id FROM retrieval.collection_create(%s, true, true, 'v1')"
    with connect_as(cluster, name, "pipeshub_indexing_login") as c:
        with uow(c, "pipeshub_indexing", ORG_A), pytest.raises(pg.InsufficientPrivilege):
            c.execute(create, ("per-app",))
        with uow(c, "pipeshub_indexing", ORG_A), pytest.raises(pg.InsufficientPrivilege):
            c.execute("INSERT INTO retrieval.collections (name, sparse_enabled, sparse_idf, lexical_version) VALUES ('x', true, true, 'v')")
        with uow(c, "pipeshub_indexing", ORG_A):
            assert c.execute("SELECT collection_id, status FROM retrieval.collection_get('records')").fetchone() == (1, "ready")
    with connect_as(cluster, name, "pipeshub_jobs") as c:
        with uow(c, "pipeshub_jobs", None):
            new = c.execute(create, ("per-app",)).fetchone()[0]
        with uow(c, "pipeshub_jobs", None), pytest.raises(pg.UniqueViolation):
            c.execute(create, ("per-app",))
        with uow(c, "pipeshub_jobs", None):
            assert c.execute("SELECT gone_id FROM retrieval.collection_mark_deleting('per-app')").fetchone()[0] == new
        with cluster.admin(name) as admin:
            assert admin.execute("SELECT * FROM retrieval.collection_get('per-app')").fetchall() == []   # hidden at once
        with uow(c, "pipeshub_jobs", None):
            assert c.execute(create, ("per-app",)).fetchone()[0] != new                                  # and the name is free again


def test_an_orgs_first_space_is_adopted_once_by_the_indexing_role(rdb):
    cluster, name = rdb
    adopt = "SELECT active_id FROM retrieval.org_space_adopt(%s, %s, 'cosine')"
    with connect_as(cluster, name, "pipeshub_indexing_login") as c:
        with uow(c, "pipeshub_indexing", ORG_A):
            first = c.execute(adopt, ("bge", 4)).fetchone()[0]
        with uow(c, "pipeshub_indexing", ORG_A):
            assert c.execute(adopt, ("other-model", 8)).fetchone()[0] == first        # the org already has one: nothing is added
        with uow(c, "pipeshub_indexing", ORG_B):
            assert c.execute(adopt, ("bge", 4)).fetchone()[0] == first               # the descriptor is shared
        with uow(c, "pipeshub_indexing", None):
            assert c.execute(adopt, ("bge", 4)).fetchall() == []                       # no org: no answer
        with uow(c, "pipeshub_indexing", ORG_B), pytest.raises(pg.InvalidParameterValue):
            c.execute(adopt, ("bge", 0))
    with cluster.admin(name) as c:
        assert c.execute("SELECT count(*) FROM retrieval.embedding_spaces").fetchone()[0] == 1
    with connect_as(cluster, name, "pipeshub_query_login") as c:
        with uow(c, "pipeshub_query", ORG_A):
            assert c.execute("SELECT space_id, space_role, model, dim FROM retrieval.org_space_list()").fetchall() == [(first, "active", "bge", 4)]
        with uow(c, "pipeshub_query", ORG_A), pytest.raises(pg.InsufficientPrivilege):          # reading services cannot adopt
            c.execute(adopt, ("bge", 4))
    with connect_as(cluster, name, "pipeshub_connectors_login") as c, uow(c, "pipeshub_connectors", ORG_A), pytest.raises(pg.InsufficientPrivilege):
        c.execute(adopt, ("bge", 4))


def replace_space(cluster, name: str, org: str, model: str, dim: int) -> bool:
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", org):
        return c.execute("SELECT replaced FROM retrieval.org_space_replace_empty(%s, %s, 'cosine')", (model, dim)).fetchone()[0]


def spaces_of(cluster, name: str, org: str) -> list[tuple]:
    with cluster.admin(name) as c:
        return c.execute("SELECT o.space_id, s.model, o.dim, o.role FROM retrieval.org_spaces o JOIN retrieval.embedding_spaces s USING (space_id) "
                         "WHERE o.org_id = %s ORDER BY o.space_id", (org,)).fetchall()


def test_an_empty_space_is_replaced_and_one_with_points_is_not(rdb):
    cluster, name = rdb
    adopt(cluster, name, ORG_A)                                                      # model 'bge', dimension 4
    for login, role in (("pipeshub_query_login", "pipeshub_query"), ("pipeshub_connectors_login", "pipeshub_connectors")):
        with connect_as(cluster, name, login) as c, uow(c, role, ORG_A), pytest.raises(pg.InsufficientPrivilege):
            c.execute("SELECT * FROM retrieval.org_space_replace_empty('m2', 8, 'cosine')")      # only the indexing service replaces a space
    assert replace_space(cluster, name, ORG_A, "m2", 8) is True
    assert spaces_of(cluster, name, ORG_A) == [(1, "bge", 0, "retiring"), (2, "m2", 8, "active")]
    with connect_as(cluster, name, "pipeshub_jobs") as c, uow(c, "pipeshub_jobs", ORG_A), pytest.raises(pg.ForeignKeyViolation):
        put(c, ORG_A, "a0000000-0000-0000-0000-000000000009")                        # a late write under the old space fails on the key
    assert replace_space(cluster, name, ORG_A, "bge", 4) is True                    # and back again
    assert spaces_of(cluster, name, ORG_A) == [(1, "bge", 4, "active"), (2, "m2", 0, "retiring")]
    with connect_as(cluster, name, "pipeshub_jobs") as c, uow(c, "pipeshub_jobs", ORG_A):
        put(c, ORG_A, "a0000000-0000-0000-0000-000000000001")
    assert replace_space(cluster, name, ORG_A, "m2", 8) is False                    # a point exists: nothing changes
    assert spaces_of(cluster, name, ORG_A) == [(1, "bge", 4, "active"), (2, "m2", 0, "retiring")]
    assert replace_space(cluster, name, ORG_B, "m2", 8) is True                     # an org without a space gets one; other orgs are untouched
    assert [r[3] for r in spaces_of(cluster, name, ORG_A)] == ["active", "retiring"]


def test_a_write_that_lands_during_the_replacement_wins_or_loses_cleanly(rdb):
    import threading
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    result: list[bool] = []
    with connect_as(cluster, name, "pipeshub_jobs") as writer:
        with writer.transaction():                                                    # the write is in flight, not committed
            writer.execute("SELECT set_config('role', 'pipeshub_jobs', true), set_config('app.org_id', %s, true)", (ORG_A,))
            put(writer, ORG_A, "a0000000-0000-0000-0000-000000000002")
            t = threading.Thread(target=lambda: result.append(replace_space(cluster, name, ORG_A, "m2", 8)))
            t.start()
            t.join(1.5)
            assert t.is_alive(), "the replacement waits for the write instead of racing it"
        t.join(10)
    assert result == [False]
    assert spaces_of(cluster, name, ORG_A) == [(1, "bge", 4, "active")]


def second_call_while_first_is_open(cluster, name, org: str, sql: str):
    """Run `sql` as the indexing service in two transactions of one org, the second starting while the first is still open:
    two workers of one org that both write for the first time."""
    import threading
    second: list = []

    def run():
        try:
            with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", org):
                second.append(c.execute(sql).fetchall())
        except Exception as e:                                                         # the test reports it
            second.append(e)
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", org):
        first = c.execute(sql).fetchall()
        t = threading.Thread(target=run)
        t.start()
        t.join(1.5)
        assert t.is_alive(), "the second call waits for the first"
    t.join(10)
    return first, second[0]


def test_two_first_writes_of_an_org_adopt_one_space_and_neither_fails(rdb):
    cluster, name = rdb
    first, second = second_call_while_first_is_open(cluster, name, ORG_A, "SELECT active_id FROM retrieval.org_space_adopt('bge', 4, 'cosine')")
    assert second == first
    assert len(spaces_of(cluster, name, ORG_A)) == 1


def test_asking_for_a_leaf_twice_at_once_is_harmless(rdb):
    cluster, name = rdb
    first, second = second_call_while_first_is_open(cluster, name, ORG_A, "SELECT requested FROM retrieval.org_leaf_request()")
    assert first == [(True,)] and second == [(True,)]
    with cluster.admin(name) as c:
        assert c.execute("SELECT count(*) FROM retrieval.placement_requests WHERE org_id = %s", (ORG_A,)).fetchone()[0] == 1


def seed_two_orgs(cluster, name):
    """Org A and org B each hold 3 points (+ text, group, member rows) in the default tree; a space of dimension 4 exists."""
    adopt(cluster, name, ORG_A)
    adopt(cluster, name, ORG_B)
    with connect_as(cluster, name, "pipeshub_jobs") as c:
        for org in (ORG_A, ORG_B):
            with uow(c, "pipeshub_jobs", org):
                for i in range(3):
                    pid = f"{'a' if org == ORG_A else 'b'}0000000-0000-0000-0000-00000000000{i}"
                    put(c, org, pid)
                    sk = c.execute("SELECT point_sk FROM retrieval.points WHERE org_id = %s AND point_id = %s", (org, pid)).fetchone()[0]
                    c.execute("INSERT INTO retrieval.point_text VALUES (%s, %s, 'text', '{}')", (org, sk))
                c.execute("INSERT INTO retrieval.point_groups VALUES (%s, 1, 'g', '{c1}', '{}', '{}', now())", (org,))
                c.execute("INSERT INTO retrieval.group_members VALUES (%s, 1, 'g', 1, 'c1')", (org,))


def test_a_point_must_have_its_spaces_dimension(rdb):
    """The database itself refuses a vector whose length is not the dimension of its space, or a space the org does not have."""
    cluster, name = rdb
    adopt(cluster, name, ORG_A, 4)
    with connect_as(cluster, name, "pipeshub_indexing_login") as c:
        with uow(c, "pipeshub_indexing", ORG_A):
            put(c, ORG_A, "00000000-0000-0000-0000-0000000000a1")
        with uow(c, "pipeshub_indexing", ORG_A), pytest.raises(pg.ForeignKeyViolation):          # 8 numbers in a 4-dimensional space
            c.execute("INSERT INTO retrieval.points (org_id, collection_id, space_id, point_id, group_key, embedding, embedding_dim) "
                      "VALUES (%s, 1, 1, '00000000-0000-0000-0000-0000000000a2', 'g', '[1,2,3,4,5,6,7,8]'::ext.halfvec, 8)", (ORG_A,))
        with uow(c, "pipeshub_indexing", ORG_A), pytest.raises(pg.ForeignKeyViolation):          # a space the org does not have
            c.execute("INSERT INTO retrieval.points (org_id, collection_id, space_id, point_id, group_key, embedding, embedding_dim) "
                      "VALUES (%s, 1, 7, '00000000-0000-0000-0000-0000000000a3', 'g', '[1,2,3,4]'::ext.halfvec, 4)", (ORG_A,))


def test_vrid_orgs_names_the_orgs_holding_a_vrid_for_the_indexing_role_only(rdb):
    cluster, name = rdb
    seed_two_orgs(cluster, name)
    a, b = "a0000000-0000-0000-0000-000000000000", "b0000000-0000-0000-0000-000000000001"
    q = "SELECT vrid, org_id FROM retrieval.vrid_orgs(%s::text[])"
    with connect_as(cluster, name, "pipeshub_indexing_login") as c:
        with uow(c, "pipeshub_indexing", None):                          # no org needed: the sweep knows none
            assert sorted(c.execute(q, ([a, b, "nobody"],)).fetchall()) == [(a, ORG_A), (b, ORG_B)]
            assert c.execute(q, ([],)).fetchall() == []
            assert c.execute(q, (["nobody"],)).fetchall() == []
        with uow(c, "pipeshub_indexing", ORG_A), pytest.raises(pg.InvalidParameterValue):
            c.execute(q, ([f"v{i}" for i in range(101)],))                # a bounded batch
    for login, role in (("pipeshub_query_login", "pipeshub_query"), ("pipeshub_connectors_login", "pipeshub_connectors")):
        with connect_as(cluster, name, login) as c, uow(c, role, ORG_A), pytest.raises(pg.InsufficientPrivilege):
            c.execute(q, ([a],))                                          # no other service can read across orgs


def test_the_connectors_role_has_no_privilege_on_the_vector_tables_but_reads_the_catalog(rdb):
    cluster, name = rdb
    with cluster.admin(name) as c:
        tables = [r[0] for r in c.execute("SELECT format('%I.%I', schemaname, tablename) FROM pg_tables WHERE schemaname = 'retrieval'")]
        held = [(t, p) for t in tables for p in ("SELECT", "INSERT", "UPDATE", "DELETE")
                if c.execute("SELECT has_table_privilege('pipeshub_connectors', %s, %s)", (t, p)).fetchone()[0]]
    assert held == []
    with connect_as(cluster, name, "pipeshub_connectors_login") as c, uow(c, "pipeshub_connectors", ORG_A):
        c.execute("SELECT * FROM retrieval.collection_list()").fetchall()                  # the health check
        with pytest.raises(pg.InsufficientPrivilege):
            c.execute("SELECT count(*) FROM retrieval.points")


def test_the_collection_stamp_is_set_once_and_read_by_every_service(rdb):
    cluster, name = rdb
    with connect_as(cluster, name, "pipeshub_indexing_login") as c:
        with uow(c, "pipeshub_indexing", None):
            assert c.execute("SELECT stamp FROM retrieval.collection_stamp_set('entities', 's0123456789a')").fetchall() == [("s0123456789a",)]
            assert c.execute("SELECT stamp FROM retrieval.collection_stamp_set('entities', 'sother')").fetchall() == [("s0123456789a",)]   # first wins
            assert c.execute("SELECT stamp FROM retrieval.collection_stamp_set('nope', 'sx')").fetchall() == []
        with uow(c, "pipeshub_indexing", None), pytest.raises(pg.InvalidParameterValue):
            c.execute("SELECT * FROM retrieval.collection_stamp_set('entities', '')")
    with connect_as(cluster, name, "pipeshub_query_login") as c:
        with uow(c, "pipeshub_query", None):
            assert c.execute("SELECT stamp FROM retrieval.collection_stamp_get('entities')").fetchall() == [("s0123456789a",)]
            assert c.execute("SELECT stamp FROM retrieval.collection_stamp_get('records')").fetchall() == []
        with uow(c, "pipeshub_query", None), pytest.raises(pg.InsufficientPrivilege):
            c.execute("SELECT * FROM retrieval.collection_stamp_set('entities', 'sx')")


def test_vrid_orgs_never_returns_a_cut_answer(rdb):
    cluster, name = rdb
    vrids = [f"vr-{i}" for i in range(100)]

    def add_org(c, i: int) -> None:
        org = f"{i + 1:024x}"
        c.execute("INSERT INTO retrieval.org_spaces (org_id, space_id, dim, role) VALUES (%s, 30001, 4, 'active')", (org,))
        c.execute("INSERT INTO retrieval.points (org_id, collection_id, space_id, point_id, group_key, embedding, embedding_dim) "
                  "SELECT %s, 1, 30001, gen_random_uuid(), 'vr-' || g, '[1,0,0,0]'::ext.halfvec, 4 FROM generate_series(0, 99) g", (org,))

    with cluster.admin(name) as c:
        c.execute("INSERT INTO retrieval.embedding_spaces (space_id, model, dim, metric) OVERRIDING SYSTEM VALUE VALUES (30001, 'm', 4, 'cosine') ON CONFLICT DO NOTHING")
        for i in range(10):
            add_org(c, i)                                                                            # ten orgs x 100 vrids: exactly 1000 answers
    q = "SELECT count(*) FROM retrieval.vrid_orgs(%s::text[])"
    with connect_as(cluster, name, "pipeshub_indexing_login") as c:
        with uow(c, "pipeshub_indexing", None):
            assert c.execute(q, (vrids,)).fetchone()[0] == 1000
    with cluster.admin(name) as c:
        add_org(c, 10)                                                                               # eleven: 1100 answers
    with connect_as(cluster, name, "pipeshub_indexing_login") as c:
        with uow(c, "pipeshub_indexing", None), pytest.raises(pg.ProgramLimitExceeded):
            c.execute(q, (vrids,))                                                                   # an error, never a silently cut answer
        with uow(c, "pipeshub_indexing", None):
            assert c.execute(q, (vrids[:90],)).fetchone()[0] == 990                                  # a smaller batch fits


# ---- the lexical leg: postings, the point count, search_sparse ------------------------------------------------------------------


def wrap(term: int) -> int:
    return term - 2**32 if term > 2**31 - 1 else term


def write_terms(c, org: str, point: str, group: str, weights: dict[int, float], coll: int = 1) -> int:
    """A point and its postings, as the provider writes them."""
    sk = c.execute("INSERT INTO retrieval.points (org_id, collection_id, space_id, point_id, group_key, embedding, embedding_dim, lex_terms) "
                   "VALUES (%s, %s, 1, %s, %s, %s::ext.halfvec, 4, %s) RETURNING point_sk", (org, coll, point, group, VEC, list(weights))).fetchone()[0]
    for term, w in weights.items():
        c.execute("INSERT INTO retrieval.postings (org_id, collection_id, space_id, term, point_sk, w) VALUES (%s, %s, 1, %s, %s, %s)",
                  (org, coll, wrap(term), sk, w))
    return sk


def delta(c, org: str, d: int, coll: int = 1) -> None:
    c.execute("INSERT INTO retrieval.lexical_df_delta (org_id, collection_id, space_id, term, d) VALUES (%s, %s, 1, -1, %s)", (org, coll, d))


def search(cluster, name, org, terms, weights=None, arms=(None, None, None, None), must=None, leg=10):
    q = ("SELECT point_sk, score FROM retrieval.search_sparse(1::smallint, %s::bigint[], %s::real[], %s::text[], %s::text[], %s::text[], "
         "%s::text[], %s::text[], %s::int)")
    with connect_as(cluster, name, "pipeshub_query_login") as c, uow(c, "pipeshub_query", org):
        return c.execute(q, (list(terms), weights or [1.0] * len(terms), *arms, must, leg)).fetchall()


def test_search_sparse_scores_like_bm25_and_applies_the_membership_arms(rdb):
    import math

    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    adopt(cluster, name, ORG_B)
    ids = {}
    with connect_as(cluster, name, "pipeshub_indexing_login") as c:
        with uow(c, "pipeshub_indexing", ORG_A):
            ids["a1"] = write_terms(c, ORG_A, "a0000000-0000-0000-0000-000000000001", "g1", {5: 2.0, 6: 1.0})
            ids["a2"] = write_terms(c, ORG_A, "a0000000-0000-0000-0000-000000000002", "g2", {5: 1.0})
            ids["a3"] = write_terms(c, ORG_A, "a0000000-0000-0000-0000-000000000003", "g3", {6: 1.0, 7: 1.0, 3_000_000_000: 4.0})   # an id above 2^31
            delta(c, ORG_A, 3)                                       # no folded counter yet: the count is the delta
        with uow(c, "pipeshub_indexing", ORG_B):
            write_terms(c, ORG_B, "b0000000-0000-0000-0000-000000000001", "g1", {5: 9.0, 6: 9.0})
            delta(c, ORG_B, 1)
    with connect_as(cluster, name, "pipeshub_jobs") as c, uow(c, "pipeshub_jobs", ORG_A):
        c.execute("INSERT INTO retrieval.group_members VALUES (%s, 1, 'g1', 1, 'conn1'), (%s, 1, 'g2', 2, 'rg1'), (%s, 1, 'g3', 3, 'root1')", (ORG_A,) * 3)

    def idf(n, df):
        return math.log((n - df + 0.5) / (df + 0.5) + 1)

    got = search(cluster, name, ORG_A, (5, 6))
    assert [r[0] for r in got] == [ids["a1"], ids["a2"], ids["a3"]]
    assert got[0][1] == pytest.approx(2.0 * idf(3, 2) + 1.0 * idf(3, 2))                 # Qdrant's IDF modifier, stored weight times query weight
    assert got[1][1] == got[2][1] == pytest.approx(1.0 * idf(3, 2))                       # a tie keeps the older point first
    assert search(cluster, name, ORG_A, (5, 6), [0.5, 2.0])[0][1] == pytest.approx(0.5 * 2.0 * idf(3, 2) + 2.0 * 1.0 * idf(3, 2))
    assert [r[0] for r in search(cluster, name, ORG_A, (5, 6), leg=2)] == [ids["a1"], ids["a2"]]
    assert [r[0] for r in search(cluster, name, ORG_A, (7,))] == [ids["a3"]]
    assert [r[0] for r in search(cluster, name, ORG_A, (3_000_000_000,))] == [ids["a3"]]
    assert search(cluster, name, ORG_A, (99,)) == []
    assert {r[0] for r in search(cluster, name, ORG_A, (5, 6, 7))} == set(ids.values())     # any number of terms
    assert len(search(cluster, name, ORG_A, [*range(1000, 1500), 5])) == 2          # 501 terms, most of them unknown
    # membership: a connector, a record group, a root group and a direct group are alternatives; must narrows all of them
    assert {r[0] for r in search(cluster, name, ORG_A, (5, 6), arms=(["conn1"], [], [], []))} == {ids["a1"]}
    assert {r[0] for r in search(cluster, name, ORG_A, (5, 6), arms=([], ["rg1"], ["root1"], []))} == {ids["a2"], ids["a3"]}
    assert {r[0] for r in search(cluster, name, ORG_A, (5, 6), arms=([], [], [], ["g3"]))} == {ids["a3"]}
    assert search(cluster, name, ORG_A, (5, 6), arms=([], [], [], [])) == []
    assert {r[0] for r in search(cluster, name, ORG_A, (5, 6), arms=(["conn1"], ["rg1"], None, None), must=["g2", "g3"])} == {ids["a2"]}
    assert {r[0] for r in search(cluster, name, ORG_A, (5, 6), must=["g1", "g3"])} == {ids["a1"], ids["a3"]}
    # another org sees only its own point; no org sees nothing
    assert len(search(cluster, name, ORG_B, (5, 6))) == 1 and search(cluster, name, ORG_B, (5, 6))[0][0] not in ids.values()
    assert search(cluster, name, None, (5, 6)) == []
    # a tombstone is also checked on the best points when a membership filter applies (the provider removes the postings at the tombstone)
    with cluster.admin(name) as c:
        c.execute("INSERT INTO retrieval.point_tombstones (org_id, point_sk) VALUES (%s, %s)", (ORG_A, ids["a1"]))
    assert [r[0] for r in search(cluster, name, ORG_A, (5, 6), must=["g1", "g2", "g3"])] == [ids["a2"], ids["a3"]]
    with connect_as(cluster, name, "pipeshub_jobs") as c, uow(c, "pipeshub_jobs", ORG_A), pytest.raises(pg.InsufficientPrivilege):
        c.execute("SELECT * FROM retrieval.search_sparse(1::smallint, '{5}', '{1}', NULL, NULL, NULL, NULL, NULL, 5)")


def test_the_postings_of_a_term_are_its_document_frequency(rdb):
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        sk = write_terms(c, ORG_A, "a0000000-0000-0000-0000-000000000001", "g1", {5: 1.0})
        delta(c, ORG_A, 1)
    first = search(cluster, name, ORG_A, (5,))
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        write_terms(c, ORG_A, "a0000000-0000-0000-0000-000000000002", "g2", {5: 1.0, 6: 1.0})
        delta(c, ORG_A, 1)
    second = search(cluster, name, ORG_A, (5,))                          # df 1 of n 1, then df 2 of n 2: the weight follows the postings at once
    assert [r[0] for r in first] == [sk] and len(second) == 2 and second[0][1] != first[0][1]


def kept_frequencies(cluster, name, org: str) -> dict[int, int]:
    """What the search reads as a term's frequency: the folded value plus the deltas."""
    with cluster.admin(name) as c:
        return dict(c.execute(
            "SELECT term, sum(n)::int FROM (SELECT term, df AS n FROM retrieval.lexical_df WHERE org_id = %s "
            "UNION ALL SELECT term, d FROM retrieval.lexical_term_delta WHERE org_id = %s) x GROUP BY term HAVING sum(n) <> 0", (org, org)))


def counted_frequencies(cluster, name, org: str) -> dict[int, int]:
    with cluster.admin(name) as c:
        return dict(c.execute("SELECT term, count(*)::int FROM retrieval.postings WHERE org_id = %s GROUP BY term", (org,)))


def postings_read(cluster, name, org, terms) -> tuple[list, int, int]:
    """A search's answer, the postings index entries it read and its probes of the kept frequencies: this transaction's own counters, so
    nothing another session does counts."""
    with connect_as(cluster, name, "pipeshub_query_login") as c, uow(c, "pipeshub_query", org):
        got = c.execute("SELECT point_sk, score FROM retrieval.search_sparse(1::smallint, %s::bigint[], %s::real[], NULL, NULL, NULL, NULL, NULL, 10)",
                        (list(terms), [1.0] * len(terms))).fetchall()
        read, probes = c.execute(
            "SELECT coalesce(sum(pg_stat_get_xact_tuples_returned(i.indexrelid)) FILTER (WHERE t.relname LIKE 'postings%%'), 0)::int, "
            "coalesce(sum(pg_stat_get_xact_numscans(i.indexrelid)) FILTER (WHERE t.relname = 'lexical_df'), 0)::int "
            "FROM pg_index i JOIN pg_class t ON t.oid = i.indrelid WHERE t.relnamespace = 'retrieval'::regnamespace").fetchone()
        return got, read, probes


def test_the_search_reads_kept_term_frequencies_and_each_posting_once(rdb):
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        sks = [write_terms(c, ORG_A, f"a0000000-0000-0000-0000-0000000000{i:02x}", f"g{i}", {5: 1.0 + i, 6: 2.0, 7 + i % 3: 0.5}) for i in range(30)]
        delta(c, ORG_A, 30)
        c.execute("DELETE FROM retrieval.postings WHERE org_id = %s AND point_sk = ANY(%s) AND term = 6", (ORG_A, sks[:4]))     # a rewrite drops a term
        c.execute("UPDATE retrieval.postings SET w = 9 WHERE org_id = %s AND point_sk = %s AND term = 5", (ORG_A, sks[5]))     # a weight changes
        c.execute("INSERT INTO retrieval.postings (org_id, collection_id, space_id, term, point_sk, w) "
                  "SELECT %s, 1, 1, 11, s, 1.0 FROM unnest(%s::bigint[]) s", (ORG_A, sks[:7]))                                     # one statement, many rows
    assert kept_frequencies(cluster, name, ORG_A) == counted_frequencies(cluster, name, ORG_A) == {5: 30, 6: 26, 7: 10, 8: 10, 9: 10, 11: 7}
    with cluster.admin(name) as c:            # other terms, so that the postings of a term are a range of the index and not the whole org
        c.execute("INSERT INTO retrieval.postings (org_id, collection_id, space_id, term, point_sk, w) "
                  "SELECT %s, 1, 1, 1000 + t, s, 1.0 FROM unnest(%s::bigint[]) s, generate_series(1, 300) t", (ORG_A, sks))
        c.execute("VACUUM (ANALYZE) retrieval.postings")                   # no dead entries of the rewrites above left in the index
    kept, _, probes = postings_read(cluster, name, ORG_A, (5, 6, 11))
    assert probes == 3                                                        # one kept frequency per term (the bounds prune the postings read)
    with cluster.admin(name) as c:                                            # a collection whose postings predate the kept frequencies
        c.execute("INSERT INTO retrieval.lexical_df_pending VALUES (%s, 1, 1)", (ORG_A,))
    counted, read, probes = postings_read(cluster, name, ORG_A, (5, 6, 11))
    assert counted == kept and read == 2 * (30 + 26 + 7) and probes == 0       # the same answer, scores included, by counting (no bounds: every posting)
    with connect_as(cluster, name, "pipeshub_jobs") as jobs:
        assert fold.run(jobs)[1] == {}
        assert kept_frequencies(cluster, name, ORG_A) == counted_frequencies(cluster, name, ORG_A)    # the fold, before recount could repair it
        assert fold.recount_org(jobs, ORG_A) is not None
    with cluster.admin(name) as c:
        assert c.execute("SELECT count(*) FROM retrieval.lexical_term_delta WHERE org_id = %s", (ORG_A,)).fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM retrieval.lexical_df_pending WHERE org_id = %s", (ORG_A,)).fetchone()[0] == 0
    assert kept_frequencies(cluster, name, ORG_A) == counted_frequencies(cluster, name, ORG_A)
    again, _, probes = postings_read(cluster, name, ORG_A, (5, 6, 11))
    assert again == kept and probes == 3
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        c.execute("DELETE FROM retrieval.postings WHERE org_id = %s AND point_sk = ANY(%s)", (ORG_A, sks[20:]))           # points deleted
    assert kept_frequencies(cluster, name, ORG_A) == counted_frequencies(cluster, name, ORG_A)
    with cluster.admin(name) as c:                                            # recount repairs a frequency that drifted
        c.execute("UPDATE retrieval.lexical_df SET df = df + 5 WHERE org_id = %s AND term = 5", (ORG_A,))
    with connect_as(cluster, name, "pipeshub_jobs") as jobs:
        assert fold.recount_org(jobs, ORG_A) is not None
    assert kept_frequencies(cluster, name, ORG_A) == counted_frequencies(cluster, name, ORG_A)


def test_a_fold_keeps_no_frequency_row_for_a_term_that_has_no_postings_left(rdb):
    """A term's postings inserted and folded, then deleted and folded: the row goes (a missing row is a frequency of 0 to the search), so
    the table does not grow with every term that ever came and went."""
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        write_terms(c, ORG_A, "a0000000-0000-0000-0000-000000000001", "g1", {5: 1.0, 6: 1.0})
    with connect_as(cluster, name, "pipeshub_jobs") as jobs:
        assert fold.run(jobs)[1] == {}
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        c.execute("DELETE FROM retrieval.postings WHERE org_id = %s AND term = 6", (ORG_A,))
    with connect_as(cluster, name, "pipeshub_jobs") as jobs:
        assert fold.run(jobs)[1] == {}
    with cluster.admin(name) as c:
        assert dict(c.execute("SELECT term, df FROM retrieval.lexical_df WHERE org_id = %s", (ORG_A,))) == {5: 1}
    assert kept_frequencies(cluster, name, ORG_A) == counted_frequencies(cluster, name, ORG_A) == {5: 1}


def bm25_reference(cluster, name, org, terms, weights, groups=None, leg=10) -> list[tuple[int, float]]:
    """The exhaustive answer, computed here from the rows: every posting of every query term summed per point, the points whose group is in
    ``groups`` (None: all), best first and the older point first on a tie."""
    import math

    with cluster.admin(name) as c:
        n = c.execute("SELECT coalesce(sum(n_points), 0) FROM retrieval.collection_stats WHERE org_id = %s", (org,)).fetchone()[0] + \
            c.execute("SELECT coalesce(sum(d), 0) FROM retrieval.lexical_df_delta WHERE org_id = %s AND term = -1", (org,)).fetchone()[0]
        rows = c.execute("SELECT q.term, q.point_sk, q.w, p.group_key FROM retrieval.postings q JOIN retrieval.points p USING (org_id, point_sk) "
                         "WHERE q.org_id = %s AND q.term = ANY(%s)", (org, list(terms))).fetchall()
    df: dict[int, int] = {}
    for t, *_ in rows:
        df[t] = df.get(t, 0) + 1
    score: dict[int, float] = {}
    for t, sk, w, g in rows:
        if groups is None or g in groups:
            f = sum(qw for qt, qw in zip(terms, weights, strict=True) if qt == t) * (math.log(max(n, df[t]) + 1) - math.log(df[t] + 0.5))
            score[sk] = score.get(sk, 0.0) + f * w
    return sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))[:leg]


def corpus(c, org: str, points: int = 400) -> list[int]:
    """A common term (every point, small weights), a medium one (every 5th point), two rare ones (every 37th, every 53rd), and a term with
    larger weights on every 3rd point; groups g0..g9 by point."""
    import random

    r = random.Random(7)
    sks = []
    for i in range(points):
        terms = {1: round(r.uniform(0.2, 2.0), 3)}
        if i % 5 == 0:
            terms[2] = round(r.uniform(0.2, 2.0), 3)
        if i % 37 == 0:
            terms[3] = round(r.uniform(0.2, 2.0), 3)
        if i % 53 == 0:
            terms[4] = round(r.uniform(0.2, 2.0), 3)
        if i % 3 == 0:
            terms[5] = round(r.uniform(1.0, 6.0), 3)
        sks.append(write_terms(c, org, f"a0000000-0000-0000-0000-{i:012d}", f"g{i % 10}", terms))
    delta(c, org, points)
    return sks


def check_against_reference(cluster, name, org, queries, scopes, legs) -> None:
    for terms, weights in queries:
        for leg in legs:
            for arms, groups in scopes:
                got = search(cluster, name, org, terms, weights, arms=arms, leg=leg)
                want = bm25_reference(cluster, name, org, terms, weights, groups, leg)
                assert [r[0] for r in got] == [r[0] for r in want], (terms, weights, leg, arms)
                assert [r[1] for r in got] == pytest.approx([r[1] for r in want], rel=1e-5, abs=1e-6), (terms, weights, leg, arms)   # f is a real


def test_a_small_collection_is_answered_by_the_exhaustive_sum(rdb):
    """Few postings in all (at most 200,000 for the query's terms): every posting is summed, as before the bounds, and the answer is the
    reference's for any query, weights (a negative one too), leg and membership."""
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        corpus(c, ORG_A)
    with connect_as(cluster, name, "pipeshub_jobs") as c, uow(c, "pipeshub_jobs", ORG_A):
        c.execute("INSERT INTO retrieval.group_members SELECT %s, 1, 'g' || g, 2, 'rg' || (g %% 5) FROM generate_series(0, 9) g", (ORG_A,))
    queries = [((1, 3), [1.0, 1.0]), ((1, 2, 3, 4), [1.0, 1.0, 1.0, 1.0]), ((1, 4), [3.0, 0.5]), ((1, 2, 5), [1.0, 0.7, 0.2]),
               ((1, 3, 4), [1.0, -0.5, 2.0]), ((1,), [1.0]), ((2, 3, 4, 5, 1), [0.3, 1.0, 1.0, 1.0, 1.0])]
    check_against_reference(cluster, name, ORG_A, queries, (((None, None, None, None), None), ((None, ["rg1"], None, None), {"g1", "g6"}),
                                                            ((None, ["rg4"], None, ["g0"]), {"g4", "g9", "g0"})), (1, 5, 30))


def big_corpus(cluster, name, org: str, points: int = 80_000) -> None:
    """Over 200,000 postings for the query below: two terms on every point, one on every 2nd, one on every 5th (all over 5 percent of
    the points), and rarer ones on every 97th, 251st and 1009th; weights spread over 0.2..2.0; groups g0..g9; the point counter."""
    with cluster.admin(name) as c:       # the triggers run as for any writer: the deltas carry the bounds
        c.execute("INSERT INTO retrieval.points (org_id, collection_id, space_id, point_id, group_key, embedding, embedding_dim, lex_terms) "
                  "SELECT %s, 1, 1, md5('big' || i)::uuid, 'g' || (i %% 10), %s::ext.halfvec, 4, '{}' FROM generate_series(1, %s) i", (org, VEC, points))
        c.execute("INSERT INTO retrieval.postings (org_id, collection_id, space_id, term, point_sk, w) "
                  "SELECT p.org_id, 1, 1, t.term, p.point_sk, 0.2 + ((p.point_sk * 7919 + t.term * 104729) %% 1801) * 0.001 "
                  "FROM retrieval.points p CROSS JOIN (VALUES (1, 1), (2, 1), (3, 2), (4, 5), (5, 97), (6, 251), (7, 1009)) t(term, every) "
                  "WHERE p.org_id = %s AND p.point_sk %% t.every = 0 ORDER BY 4, 5", (org,))
        c.execute("INSERT INTO retrieval.lexical_df_delta (org_id, collection_id, space_id, term, d) VALUES (%s, 1, 1, -1, %s)", (org, points))
        c.execute("VACUUM (ANALYZE) retrieval.points, retrieval.postings")


def test_a_large_collection_is_answered_exactly_by_pruning_and_by_scoring_narrow_arms_directly(rdb):
    """Over 200,000 postings: the common terms stay out of the candidates (the whole org and broad arms) or the points of narrow arms are
    scored directly; either way the answer is the reference's, and far fewer postings are read than the query's terms hold. A common
    term's posting whose weight is raised past every other score makes its point the best answer, which only a raised bound lets in."""
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    big_corpus(cluster, name, ORG_A)
    with connect_as(cluster, name, "pipeshub_jobs") as c, uow(c, "pipeshub_jobs", ORG_A):
        c.execute("INSERT INTO retrieval.group_members SELECT %s, 1, 'g' || g, 2, 'rg' || (g %% 5) FROM generate_series(0, 9) g "
                  "UNION ALL SELECT %s, 1, 'g' || g, 1, 'conn' FROM generate_series(0, 9) g", (ORG_A, ORG_A))
    terms = (1, 2, 3, 4, 5, 6, 7)
    queries = [(terms, [1.0] * 7), (terms, [0.4, 1.5, 0.3, 1.0, 1.2, 0.9, 1.1]), (terms, [1.0, 1.0, -0.5, 1.0, 2.0, 0.5, 1.0])]
    # arms of 16,000 points (rg1: g1 and g6) take the pruned path, of 8,000 (g0 alone) are scored directly (100,000 / 7 terms = 14,285)
    check_against_reference(cluster, name, ORG_A, queries, (((None, None, None, None), None), ((["conn"], None, None, None), None),
                                                            ((None, ["rg1"], None, None), {"g1", "g6"}), ((None, None, None, ["g0"]), {"g0"})), (1, 10, 40))
    _, read = postings_scanned(cluster, name, ORG_A, terms)
    assert read < 20_000                                    # of 216,000: the rare terms' postings and a probe per candidate and common term
    with cluster.admin(name) as c:
        sk = c.execute("SELECT point_sk FROM retrieval.points WHERE org_id = %s AND point_sk %% 5 = 0 AND point_sk %% 97 <> 0 "
                       "AND point_sk %% 251 <> 0 AND point_sk %% 1009 <> 0 ORDER BY point_sk LIMIT 1", (ORG_A,)).fetchone()[0]
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):     # term 4: every 5th point, left out
        c.execute("UPDATE retrieval.postings SET w = 1000 WHERE org_id = %s AND point_sk = %s AND term = 4", (ORG_A, sk))
    assert search(cluster, name, ORG_A, terms)[0][0] == sk
    check_against_reference(cluster, name, ORG_A, queries[:1], (((None, None, None, None), None), ((None, None, None, ["g0", "g1"]), {"g0", "g1"})), (10,))


def postings_scanned(cluster, name, org, terms) -> tuple[list, int]:
    """A whole-org search's answer (leg 10) and the rows its scans of the postings returned, table and index. Sequential scans are off: on a
    collection this small the planner may read a term's postings by scanning the whole table, which says nothing about the rows the search
    asks for (a large one reads them by index)."""
    with connect_as(cluster, name, "pipeshub_query_login") as c, uow(c, "pipeshub_query", org):
        c.execute("SET LOCAL enable_seqscan = off")
        got = c.execute("SELECT point_sk, score FROM retrieval.search_sparse(1::smallint, %s::bigint[], %s::real[], NULL, NULL, NULL, NULL, NULL, 10)",
                        (list(terms), [1.0] * len(terms))).fetchall()
        read = c.execute("SELECT sum(pg_stat_get_xact_tuples_returned(c.oid))::int FROM pg_class c "
                         "WHERE c.relnamespace = 'retrieval'::regnamespace AND c.relname LIKE 'postings%%'").fetchone()[0]
        return got, read


def pending_bounds(cluster, name) -> list[tuple]:
    with cluster.admin(name) as c:
        return c.execute("SELECT org_id, collection_id, space_id FROM retrieval.lexical_bound_pending ORDER BY 1, 2, 3").fetchall()


def test_the_fold_worker_fills_the_bounds_an_upgrade_left_missing_and_the_search_then_prunes(rdb, monkeypatch):
    """A collection upgraded with frequency rows that have no bound is searched by the exhaustive sum; each fold pass fills a few hundred
    bounds from the postings, the collection leaves the pending list when none is missing, and the search prunes with the same answer.
    A done job is a no-op, and a delta folded without a bound (a writer on the old trigger body) puts the collection back."""
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    big_corpus(cluster, name, ORG_A)
    terms = (1, 2, 3, 4, 5, 6, 7)
    with connect_as(cluster, name, "pipeshub_jobs") as jobs:
        assert fold.run(jobs)[1] == {}
    exact = bounds(cluster, name, ORG_A)
    assert pending_bounds(cluster, name) == [] and None not in exact.values()      # written after the upgrade: nothing to fill
    with cluster.admin(name) as c:                    # as an upgrade leaves them: a writer that does not keep the bounds lists the collection
        c.execute("UPDATE retrieval.lexical_df SET wmax = NULL WHERE org_id = %s", (ORG_A,))
    assert pending_bounds(cluster, name) == [(ORG_A, 1, 1)]
    want, read = postings_scanned(cluster, name, ORG_A, terms)
    assert read > 200_000                                                           # no bounds: every posting summed
    monkeypatch.setattr(fold, "BOUND_TERMS", 3)                                     # 7 terms: three transactions, the last one ends the job
    with connect_as(cluster, name, "pipeshub_jobs") as jobs:
        assert fold.fill_bounds(jobs) == ({ORG_A: 7}, {}, 0)
        assert fold.fill_bounds(jobs) == ({}, {}, 0)                                # done: a no-op
    assert bounds(cluster, name, ORG_A) == exact and pending_bounds(cluster, name) == []
    got, read = postings_scanned(cluster, name, ORG_A, terms)
    assert [r[0] for r in got] == [r[0] for r in want] and [r[1] for r in got] == pytest.approx([r[1] for r in want]) and read < 20_000
    with cluster.admin(name) as c:                                                  # an older writer's delta: not known again until filled
        c.execute("INSERT INTO retrieval.lexical_term_delta (org_id, collection_id, space_id, term, d) VALUES (%s, 1, 1, 4, 0)", (ORG_A,))
    monkeypatch.setenv("PG_JOBS_URL", f"postgresql://pipeshub_jobs@127.0.0.1:{cluster.port}/{name}")
    from db import maintain
    assert maintain.main(["fold"]) == 0                                             # folds it, marks the collection, fills it in the same pass
    assert bounds(cluster, name, ORG_A) == exact and pending_bounds(cluster, name) == []


def bounds(cluster, name, org: str) -> dict[int, float | None]:
    """The bound the search reads per term: the folded value and the deltas', not known when any of them is not."""
    with cluster.admin(name) as c:
        return dict(c.execute(
            "SELECT term, CASE WHEN bool_or(u) THEN NULL ELSE max(w) END FROM (SELECT term, wmax AS w, wmax IS NULL AS u FROM retrieval.lexical_df WHERE org_id = %s "
            "UNION ALL SELECT term, wmax, wmax IS NULL FROM retrieval.lexical_term_delta WHERE org_id = %s) x GROUP BY term", (org, org)))


def test_a_terms_bound_follows_every_weight_written_through_folds_and_recount(rdb):
    """A weight raised by an update raises the bound (a common term whose one posting grew must still win the search), a delete leaves it,
    the fold keeps the larger, a delta without a bound (written before retrieval_0008) makes it not known until recount, which makes it exact."""
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        sks = [write_terms(c, ORG_A, f"a0000000-0000-0000-0000-{i:012d}", f"g{i}", {1: 0.1, **({2: 1.0} if i < 15 else {})}) for i in range(60)]
        delta(c, ORG_A, 60)
    assert bounds(cluster, name, ORG_A) == {1: pytest.approx(0.1), 2: 1.0}
    assert sks[50] not in [r[0] for r in search(cluster, name, ORG_A, (1, 2))]
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        c.execute("UPDATE retrieval.postings SET w = 1000 WHERE org_id = %s AND point_sk = %s AND term = 1", (ORG_A, sks[50]))
        c.execute("DELETE FROM retrieval.postings WHERE org_id = %s AND point_sk = %s AND term = 2", (ORG_A, sks[0]))
    assert bounds(cluster, name, ORG_A) == {1: 1000.0, 2: 1.0}
    assert search(cluster, name, ORG_A, (1, 2))[0][0] == sks[50]            # without the raised bound the common term would be left out
    with connect_as(cluster, name, "pipeshub_jobs") as jobs:
        assert fold.run(jobs)[1] == {}
    with cluster.admin(name) as c:
        assert dict(c.execute("SELECT term, wmax FROM retrieval.lexical_df WHERE org_id = %s", (ORG_A,))) == {1: 1000.0, 2: 1.0}
        c.execute("INSERT INTO retrieval.lexical_term_delta (org_id, collection_id, space_id, term, d) VALUES (%s, 1, 1, 2, 0)", (ORG_A,))
    assert bounds(cluster, name, ORG_A)[2] is None                          # a delta of an older writer: not known, the term is summed in full
    assert [r[0] for r in search(cluster, name, ORG_A, (1, 2))] == [r[0] for r in bm25_reference(cluster, name, ORG_A, (1, 2), [1.0, 1.0])]
    with connect_as(cluster, name, "pipeshub_jobs") as jobs:
        assert fold.run(jobs)[1] == {}
    assert bounds(cluster, name, ORG_A) == {1: 1000.0, 2: None}              # the fold keeps it not known
    with cluster.admin(name) as c:
        c.execute("INSERT INTO retrieval.lexical_term_delta (org_id, collection_id, space_id, term, d) VALUES (%s, 1, 1, 1, 0)", (ORG_A,))
        c.execute("UPDATE retrieval.postings SET w = 0.5 WHERE org_id = %s AND point_sk = %s AND term = 1", (ORG_A, sks[50]))
    with connect_as(cluster, name, "pipeshub_jobs") as jobs:
        assert fold.recount_org(jobs, ORG_A) is not None
        assert bounds(cluster, name, ORG_A) == {1: None, 2: 1.0}             # exact from the postings, but a delta without one keeps it not known
        assert fold.run(jobs)[1] == {} and fold.fill_bounds(jobs)[1] == {}     # until it is folded, and the fill computes it
    assert bounds(cluster, name, ORG_A) == {1: 0.5, 2: 1.0} and pending_bounds(cluster, name) == []
    assert kept_frequencies(cluster, name, ORG_A) == counted_frequencies(cluster, name, ORG_A)


def safe(bound: float | None, w: float) -> bool:
    """A bound the search may prune on must not be below a weight a posting of the term has; not known is always safe."""
    return bound is None or bound >= w


def test_a_recount_leaves_the_deltas_without_a_bound_not_known_until_they_are_folded(rdb, monkeypatch):
    """A posting replaced by one of weight 1000 before the upgrade leaves two deltas without a bound. Recount bounds the folded row at 1000;
    if the fold then takes the deletion alone, the row goes, and the insertion must not be left with a bound below 1000, between the two
    batches or after the second one."""
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        sk = write_terms(c, ORG_A, "a0000000-0000-0000-0000-000000000001", "g1", {5: 1.0})
    with connect_as(cluster, name, "pipeshub_jobs") as jobs:
        assert fold.run(jobs)[1] == {}
    with cluster.admin(name) as c:                    # as the trigger of the earlier release wrote them: deltas without a bound
        c.execute("SET session_replication_role = replica")
        c.execute("UPDATE retrieval.postings SET w = 1000 WHERE org_id = %s AND point_sk = %s AND term = 5", (ORG_A, sk))
        c.execute("INSERT INTO retrieval.lexical_term_delta (org_id, collection_id, space_id, term, d) VALUES (%s, 1, 1, 5, -1), (%s, 1, 1, 5, 1)",
                  (ORG_A, ORG_A))
    with connect_as(cluster, name, "pipeshub_jobs") as jobs:
        assert fold.recount_org(jobs, ORG_A) is not None
        assert safe(bounds(cluster, name, ORG_A)[5], 1000)
        monkeypatch.setattr(fold, "BATCH", 1)
        assert fold.fold_org(jobs, ORG_A) == 1        # the deletion alone: the folded row goes
        assert safe(bounds(cluster, name, ORG_A).get(5), 1000)
        assert fold.fold_org(jobs, ORG_A) == 1        # the insertion
        assert safe(bounds(cluster, name, ORG_A)[5], 1000)
        assert fold.fill_bounds(jobs)[1] == {}
    assert bounds(cluster, name, ORG_A) == {5: 1000.0}


def test_a_fold_or_recount_that_does_not_keep_the_bounds_leaves_them_not_known_and_listed(rdb):
    """A fold of an earlier release (no keeps_bounds): its upsert of a frequency row, and its deletion of a delta whose larger weight nets to
    no change of the frequency, both leave the term not known and its collection listed; the fold worker then fills it again."""
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        sk = write_terms(c, ORG_A, "a0000000-0000-0000-0000-000000000001", "g1", {5: 1.0, 6: 1.0})
    with connect_as(cluster, name, "pipeshub_jobs") as jobs:
        assert fold.run(jobs)[1] == {}
    assert bounds(cluster, name, ORG_A) == {5: 1.0, 6: 1.0} and pending_bounds(cluster, name) == []
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        c.execute("UPDATE retrieval.postings SET w = 1000 WHERE org_id = %s AND point_sk = %s AND term = 5", (ORG_A, sk))
    with connect_as(cluster, name, "pipeshub_jobs") as jobs, jobs.transaction():       # what the earlier release's fold runs
        jobs.execute("DELETE FROM retrieval.lexical_term_delta WHERE org_id = %s", (ORG_A,))
        jobs.execute("INSERT INTO retrieval.lexical_df AS f (org_id, collection_id, space_id, term, df) VALUES (%s, 1, 1, 6, 0) "
                     "ON CONFLICT (org_id, collection_id, space_id, term) DO UPDATE SET df = f.df + EXCLUDED.df", (ORG_A,))
    assert bounds(cluster, name, ORG_A) == {5: None, 6: None} and pending_bounds(cluster, name) == [(ORG_A, 1, 1)]
    with connect_as(cluster, name, "pipeshub_jobs") as jobs:
        assert fold.fill_bounds(jobs) == ({ORG_A: 2}, {}, 0)
    assert bounds(cluster, name, ORG_A) == {5: 1000.0, 6: 1.0}


def tied_corpus(cluster, name, org: str, points: int) -> list[int]:
    """``points`` points with one term of weight 1.0: every score ties, so the answer's order is by point. Their point_sks, in order."""
    with cluster.admin(name) as c:
        c.execute("INSERT INTO retrieval.points (org_id, collection_id, space_id, point_id, group_key, embedding, embedding_dim, lex_terms) "
                  "SELECT %s, 1, 1, md5('tie' || i)::uuid, 'g' || i, %s::ext.halfvec, 4, '{9}' FROM generate_series(1, %s) i", (org, VEC, points))
        c.execute("INSERT INTO retrieval.postings (org_id, collection_id, space_id, term, point_sk, w) "
                  "SELECT org_id, 1, 1, 9, point_sk, 1.0 FROM retrieval.points WHERE org_id = %s ORDER BY point_sk", (org,))
        c.execute("INSERT INTO retrieval.lexical_df_delta (org_id, collection_id, space_id, term, d) VALUES (%s, 1, 1, -1, %s)", (org, points))
        c.execute("VACUUM (ANALYZE) retrieval.points, retrieval.postings")
        return [r[0] for r in c.execute("SELECT point_sk FROM retrieval.points WHERE org_id = %s ORDER BY point_sk", (org,))]


def searched_and_probed(cluster, name, org) -> tuple[list, int]:
    """The unfiltered one-term search for p_leg 1, and the rows its probes of the points returned (this transaction's own counters)."""
    with connect_as(cluster, name, "pipeshub_query_login") as c, uow(c, "pipeshub_query", org):
        got = c.execute("SELECT point_sk FROM retrieval.search_sparse(1::smallint, '{9}', '{1}', NULL, NULL, NULL, NULL, NULL, 1)").fetchall()
        probed = c.execute("SELECT sum(pg_stat_get_xact_tuples_returned(i.indexrelid))::int FROM pg_index i JOIN pg_class t ON t.oid = i.indrelid "
                           "WHERE t.relnamespace = 'retrieval'::regnamespace AND t.relname LIKE 'points%%'").fetchone()[0]
        return got, probed


def test_a_full_answer_in_final_order_ends_the_search_and_a_narrowing_that_admits_nothing_answers_nothing(rdb):
    """No term left out: the candidates come in the answer's order, ties by point, so the first p_leg that pass are the answer and the
    search stops there instead of checking every tied candidate; a tombstoned first candidate (its posting still there) is skipped."""
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    sks = tied_corpus(cluster, name, ORG_A, 3000)
    got, probed = searched_and_probed(cluster, name, ORG_A)
    assert got == [(sks[0],)] and probed < 50                       # one window of candidates checked, not 3,000
    assert search(cluster, name, ORG_A, (9,), must=["nobody"], leg=1) == []
    with cluster.admin(name) as c:
        c.execute("INSERT INTO retrieval.point_tombstones (org_id, point_sk) VALUES (%s, %s)", (ORG_A, sks[0]))
    got, probed = searched_and_probed(cluster, name, ORG_A)
    assert got == [(sks[1],)] and probed < 50                       # the next live point, still from the first window


def test_a_search_that_admits_nothing_reads_its_candidates_in_windows_of_bounded_size(rdb):
    """A narrowing that admits none of 20,000 candidates sets no answer to stop at, so every candidate is checked; the windows double up
    to 4,096 candidates and stay there (they doubled without end: a window of half the candidates)."""
    import re

    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    tied_corpus(cluster, name, ORG_A, 20_000)
    notes: list[str] = []
    with cluster.admin(name) as a:
        a.add_notice_handler(lambda d: notes.append(d.message_primary or ""))
        with a.transaction():
            try:
                a.execute("LOAD 'auto_explain'")
            except pg.UndefinedFile:
                pytest.fail("this test reads the search's plans through auto_explain, which the PostgreSQL build in MIGRATE_TEST_PG_BINS "
                            "lacks: install its contrib modules (backend/db/README.md, Tests)")
            for setting in ("log_min_duration = 0", "log_analyze = on", "log_timing = off", "log_nested_statements = on", "log_level = notice"):
                a.execute(f"SET LOCAL auto_explain.{setting}")
            a.execute("SET LOCAL ROLE pipeshub_query")
            a.execute("SELECT set_config('app.org_id', %s, true)", (ORG_A,))
            assert a.execute("SELECT * FROM retrieval.search_sparse(1::smallint, '{9}', '{1}', NULL, NULL, NULL, NULL, '{nobody}', 1)").fetchall() == []
            a.execute("RESET ROLE")
    windows = [int(n) for p in notes for n in re.findall(r"Function Scan on c .*?\(actual[^)]*?rows=(\d+)", p)]
    first = next(i for i in range(len(windows) + 1) if sum(windows[:i]) >= 128)
    assert sum(windows[:first]) == 128 and sum(windows[first:]) == 20_000, windows      # the capped pass's 128, then every candidate once
    assert max(windows) == 4096, windows                                                 # none in a window above the cap


def test_the_upgrade_to_the_term_bounds_takes_its_locks_in_the_order_a_running_fold_does(rdb):
    """A fold running during the upgrade holds the count deltas and then asks for the term deltas: the upgrade waits for it instead of
    holding the term tables while it waits (a deadlock, which aborts one of the two)."""
    import time

    cluster, name = rdb
    url = cluster.url(name, "pipeshub_migrate")
    r = run_migrate(url, "downgrade", "retrieval_0007", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    proc = None
    try:
        with connect_as(cluster, name, "pipeshub_jobs") as fold_tx, fold_tx.transaction():
            fold_tx.execute("SET LOCAL lock_timeout = '20s'")
            fold_tx.execute("DELETE FROM retrieval.lexical_df_delta WHERE org_id = %s", (ORG_A,))        # what FOLD takes first
            proc = start_migrate(url)
            fold_pid = fold_tx.info.backend_pid
            with cluster.admin(name) as a:
                for _ in range(600):           # the upgrade's own session, in this database, blocked by this fold
                    if a.execute("SELECT count(*) FROM pg_stat_activity WHERE usename = 'pipeshub_migrate' AND datname = %s "
                                 "AND %s = ANY(pg_blocking_pids(pid))", (name, fold_pid)).fetchone()[0]:
                        break
                    time.sleep(0.05)
                else:
                    raise AssertionError("the upgrade never waited for the fold")
            fold_tx.execute("DELETE FROM retrieval.lexical_term_delta WHERE org_id = %s", (ORG_A,))   # then FOLD_TERMS: no deadlock
        out, _ = proc.communicate(timeout=120)
        assert proc.returncode == 0, out
    finally:
        if proc is not None and proc.poll() is None:      # a failure anywhere above: the upgrade never outlives the test
            proc.kill()
            proc.communicate()


def test_downgrading_the_term_bounds_restores_the_previous_search_and_trigger_and_an_upgrade_brings_them_back(rdb):
    cluster, name = rdb
    url = cluster.url(name, "pipeshub_migrate")

    def state() -> tuple:
        with cluster.admin(name) as a:
            return (a.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema = 'retrieval' AND column_name = 'wmax'").fetchone()[0]
                + a.execute("SELECT count(*) FROM pg_class WHERE oid = to_regclass('retrieval.lexical_bound_pending')").fetchone()[0],
                    a.execute(f"SELECT prosrc LIKE '%%v_pj%%' FROM pg_proc WHERE oid = '{SPARSE}'::regprocedure").fetchone()[0],
                    a.execute("SELECT prosrc LIKE '%%wmax%%' FROM pg_proc WHERE oid = 'retrieval.postings_df_capture()'::regprocedure").fetchone()[0],
                    a.execute("SELECT count(*) FROM kernel.rls_violations").fetchone()[0])

    assert state() == (3, True, True, 0)
    r = run_migrate(url, "downgrade", "retrieval_0007", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    assert state() == (0, False, False, 0)
    adopt(cluster, name, ORG_A)
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        sk = write_terms(c, ORG_A, "a0000000-0000-0000-0000-000000000001", "g1", {5: 1.0})       # the older trigger writes no bound
        delta(c, ORG_A, 1)
    assert [r[0] for r in search(cluster, name, ORG_A, (5,))] == [sk]
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    assert state() == (3, True, True, 0)
    assert bounds(cluster, name, ORG_A) == {5: None}                          # written before the upgrade: not known until filled
    assert pending_bounds(cluster, name) == [(ORG_A, 1, 1)]                   # the upgrade lists the collection for the fold worker
    assert [r[0] for r in search(cluster, name, ORG_A, (5,))] == [sk]
    with connect_as(cluster, name, "pipeshub_jobs") as jobs:
        assert fold.run(jobs)[1] == {} and fold.fill_bounds(jobs) == ({ORG_A: 1}, {}, 0)
    assert bounds(cluster, name, ORG_A) == {5: 1.0} and pending_bounds(cluster, name) == []


def test_every_table_the_lexical_search_reads_is_filtered_by_the_bound_org():
    """Static check: the function takes the org from the unit of work, so every reference to a retrieval table must say so."""
    import re
    from pathlib import Path

    from db.lib import vetted

    fn = next(f for f in vetted.load(Path(__file__).parents[1] / "versions/retrieval/vetted/manifest.yaml") if f.name == "search_sparse")
    refs = re.findall(r"\b(?:FROM|JOIN)\s+retrieval\.(\w+)\s+(\w+)", fn.body)
    assert {t for t, _ in refs} == {"org_spaces", "collection_stats", "lexical_df_delta", "postings", "points", "group_members", "point_tombstones",
                                    "lexical_df", "lexical_term_delta", "lexical_df_pending"}
    assert scopes_without_org(fn.body) == []
    assert "p_org" not in fn.body and all("org" not in a.name for a in fn.args)               # no argument can name an org


def scopes_without_org(body: str) -> list[str]:
    """The retrieval table references of a function body whose own query scope (from the reference to the end of its parenthesised
    query, or of its statement) has no ``<alias>.org_id = v_org``: one alias reused in two subqueries is checked in each."""
    import re

    body = re.sub(r"--[^\n]*", "", body)
    missing = []
    for m in re.finditer(r"\b(?:FROM|JOIN)\s+retrieval\.(\w+)\s+(\w+)", body):
        depth, end = 0, len(body)
        for i in range(m.end(), len(body)):
            depth += {"(": 1, ")": -1}.get(body[i], 0)
            if depth < 0 or (depth == 0 and body[i] == ";"):
                end = i
                break
        if not re.search(rf"\b{m[2]}\.org_id = v_org\b", body[m.end():end]):
            missing.append(f"{m[1]} {m[2]}")
    return missing


def test_the_org_check_of_a_table_reference_looks_only_inside_its_own_query():
    body = ("SELECT (SELECT 1 FROM retrieval.lexical_df_pending x WHERE x.collection_id = 1) INTO v_kept;\n"
            "SELECT 1 FROM retrieval.points p WHERE p.org_id = v_org AND NOT EXISTS (SELECT 1 FROM retrieval.point_tombstones x "
            "WHERE x.org_id = v_org AND x.point_sk = p.point_sk);")
    assert scopes_without_org(body) == ["lexical_df_pending x"]
    assert scopes_without_org(body.replace("WHERE x.collection_id", "WHERE x.org_id = v_org AND x.collection_id")) == []


def test_the_membership_path_widens_to_every_point_when_the_best_ones_do_not_pass(rdb):
    """Leg 1 checks the best 8 first; here only the lowest-scoring of 20 points passes, so the last pass (no bound) must find it."""
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        sks = [write_terms(c, ORG_A, f"a0000000-0000-0000-0000-0000000000{i:02d}", f"g{i}", {5: 2.0 - i / 100}) for i in range(20)]
        delta(c, ORG_A, 20)
    with connect_as(cluster, name, "pipeshub_jobs") as c, uow(c, "pipeshub_jobs", ORG_A):
        c.execute("INSERT INTO retrieval.group_members VALUES (%s, 1, 'g19', 1, 'only')", (ORG_A,))
    assert [r[0] for r in search(cluster, name, ORG_A, (5,), arms=(["only"], [], [], []), leg=1)] == [sks[19]]
    assert search(cluster, name, ORG_A, (5,), arms=(["nobody"], [], [], []), leg=1) == []


@pytest.mark.parametrize("points", [7, 8, 9])
def test_the_lexical_search_goes_over_every_point_exactly_when_the_best_ones_may_not_be_all(rdb, points):
    """Leg 1 checks the best 8 first. With fewer than 8 candidates they are all of them; with 8 or more there may be more, so the last pass
    (no bound) runs: only the lowest-scoring point passes, and it is found whatever the count, and nothing is found when none passes."""
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        sks = [write_terms(c, ORG_A, f"a0000000-0000-0000-0000-0000000000{i:02d}", f"g{i}", {5: 2.0 - i / 100}) for i in range(points)]
        delta(c, ORG_A, points)
    with connect_as(cluster, name, "pipeshub_jobs") as c, uow(c, "pipeshub_jobs", ORG_A):
        c.execute("INSERT INTO retrieval.group_members VALUES (%s, 1, %s, 1, 'only')", (ORG_A, f"g{points - 1}"))
    assert [r[0] for r in search(cluster, name, ORG_A, (5,), arms=(["only"], [], [], []), leg=1)] == [sks[-1]]
    assert search(cluster, name, ORG_A, (5,), arms=(["nobody"], [], [], []), leg=1) == []
    assert [r[0] for r in search(cluster, name, ORG_A, (5,), leg=points)] == sks                      # whole org: best first, in one pass


SPARSE = "retrieval.search_sparse(smallint, bigint[], real[], text[], text[], text[], text[], text[], int)"


def test_downgrading_the_cheaper_lexical_search_keeps_the_function_and_an_upgrade_replaces_it(rdb):
    """retrieval_0003 replaces the body of a function retrieval_0001 owns: a downgrade keeps the function and its registry row
    (retrieval_0001 runs the same generated file), and an upgrade from there replaces it again with nothing left over."""
    cluster, name = rdb
    url = cluster.url(name, "pipeshub_migrate")

    def state() -> tuple:
        with cluster.admin(name) as a:
            return (a.execute("SELECT count(*) FROM pg_proc WHERE pronamespace = 'retrieval'::regnamespace AND proname = 'search_sparse'").fetchone()[0],
                    a.execute(f"SELECT count(*) FROM kernel.function_registry WHERE proc = '{SPARSE}'::regprocedure").fetchone()[0],
                    a.execute(f"SELECT prosrc LIKE '%%agg AS MATERIALIZED%%' FROM pg_proc WHERE oid = '{SPARSE}'::regprocedure").fetchone()[0],
                    a.execute("SELECT count(*) FROM kernel.rls_violations").fetchone()[0])

    assert state() == (1, 1, False, 0)
    r = run_migrate(url, "downgrade", "retrieval_0002", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    assert state() == (1, 1, False, 0)
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    assert state() == (1, 1, False, 0)
    adopt(cluster, name, ORG_A)
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        sk = write_terms(c, ORG_A, "a0000000-0000-0000-0000-000000000001", "g1", {5: 1.0})
        delta(c, ORG_A, 1)
    assert [r[0] for r in search(cluster, name, ORG_A, (5,))] == [sk]                  # the query role can still call it


def test_a_space_check_is_written_and_read_by_its_own_org_only_and_the_lock_classes_are_registered(rdb):
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    adopt(cluster, name, ORG_B)
    save = "INSERT INTO retrieval.space_checks (org_id, space_id, recall, samples, checked_at) VALUES (%s, 1, 0.97, 50, now())"
    for login, svc in (("pipeshub_indexing_login", "pipeshub_indexing"), ("pipeshub_query_login", "pipeshub_query")):
        with connect_as(cluster, name, login) as c:
            with uow(c, svc, ORG_A):
                c.execute(save + " ON CONFLICT (org_id, space_id) DO UPDATE SET recall = 0.97", (ORG_A,))
            with uow(c, svc, ORG_A):
                assert c.execute("SELECT org_id, recall FROM retrieval.space_checks").fetchall() == [(ORG_A, pytest.approx(0.97))]
            with uow(c, svc, ORG_B):
                assert c.execute("SELECT count(*) FROM retrieval.space_checks").fetchone()[0] == 0           # another org's row is invisible
            with uow(c, svc, ORG_B), pytest.raises((pg.InsufficientPrivilege, pg.CheckViolation)):             # and cannot be written
                c.execute(save, (ORG_A,))
    with connect_as(cluster, name, "pipeshub_node_login") as c, uow(c, "pipeshub_node", ORG_A), pytest.raises(pg.InsufficientPrivilege):
        c.execute("SELECT 1 FROM retrieval.space_checks")
    with cluster.admin(name) as c:
        assert {r[0] for r in c.execute("SELECT class_id FROM kernel.lock_keys")} >= {7361, 7362, 7363, 7366, 7370}


def test_an_upgrade_without_pgvector_stops_with_a_message_and_changes_nothing(db):
    """An installed database that has no retrieval module yet and no pgvector: the run says what to do and rolls back."""
    cluster, name = db
    url = cluster.url(name, "pipeshub_migrate")
    assert run_migrate(url).returncode == 0
    assert run_migrate(url, "downgrade", "retrieval@base", PG_MIGRATE_ALLOW_DOWNGRADE="1").returncode == 0
    with cluster.admin(name) as c:
        before = c.execute("SELECT branch, epoch FROM kernel.schema_epoch ORDER BY 1").fetchall()
        c.execute("DROP EXTENSION vector")
    r = run_migrate(url)
    assert r.returncode == 1 and "the vector extension is not installed" in r.stdout and "CREATE EXTENSION vector WITH SCHEMA ext" in r.stdout, r.stdout
    with cluster.admin(name) as c:
        assert c.execute("SELECT to_regnamespace('retrieval')").fetchone()[0] is None
        assert c.execute("SELECT branch, epoch FROM kernel.schema_epoch WHERE branch <> 'retrieval' ORDER BY 1").fetchall() == [x for x in before if x[0] != "retrieval"]
        c.execute("CREATE EXTENSION vector WITH SCHEMA public")                    # installed, but not where the module looks
    r = run_migrate(url)
    assert r.returncode == 1 and "must be in schema ext" in r.stdout, r.stdout
    with cluster.admin(name) as c:
        c.execute("DROP EXTENSION vector")
        c.execute("CREATE EXTENSION vector WITH SCHEMA ext")
    assert run_migrate(url).returncode == 0                                       # the fix is a plain re-run


def identity_value(cluster, name) -> int:
    with cluster.admin(name) as c:
        return c.execute("SELECT last_value FROM retrieval.embedding_spaces_space_id_seq").fetchone()[0]


def test_adopting_or_replacing_to_an_existing_descriptor_spends_no_identity_value(rdb):
    """The embedding-space id is a smallint identity (32,767 values): an insert that hits the unique key still spends one, so an
    organisation that joins an existing (model, dimension, metric) must find the descriptor first and insert only when it is missing."""
    cluster, name = rdb
    orgs = [f"{n:024x}" for n in range(1, 9)]
    adopt(cluster, name, orgs[0])
    base = identity_value(cluster, name)
    for org in orgs[1:5]:
        adopt(cluster, name, org)                                                    # the same descriptor, four more organisations
    assert identity_value(cluster, name) == base
    for org in orgs[5:]:
        assert replace_space(cluster, name, org, "bge", 4) is True                    # replacing to it, for organisations that hold nothing
    assert identity_value(cluster, name) == base
    assert replace_space(cluster, name, orgs[0], "other", 4) is True                 # a new descriptor does take a value
    assert identity_value(cluster, name) == base + 1
    assert {r[0] for r in spaces_of(cluster, name, orgs[1])} == {1}


def test_an_unfiltered_sparse_search_never_returns_a_tombstoned_point_whose_postings_remain(rdb):
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    with connect_as(cluster, name, "pipeshub_jobs") as c, uow(c, "pipeshub_jobs", ORG_A):
        live = write_terms(c, ORG_A, "a0000000-0000-0000-0000-000000000001", "r1", {10: 1.0})
        gone = write_terms(c, ORG_A, "a0000000-0000-0000-0000-000000000002", "r2", {10: 2.0})       # the better match
        delta(c, ORG_A, 2)
        c.execute("INSERT INTO retrieval.point_tombstones (org_id, point_sk) VALUES (%s, %s)", (ORG_A, gone))
    assert [r[0] for r in search(cluster, name, ORG_A, [10])] == [live]               # no filter
    assert [r[0] for r in search(cluster, name, ORG_A, [10], arms=(["c"], None, None, None))] == []   # a filter that admits nothing here


def test_downgrading_the_scope_fields_removes_the_path_and_scope_members_of_every_org_and_upgrades_again(rdb):
    """retrieval_0002 -> retrieval_0001 runs as the migration role: kind 4 and 5 members of every org are gone, the old check is back, the columns are gone,
    and an upgrade restores the wide check and the columns."""
    cluster, name = rdb
    seed_two_orgs(cluster, name)
    with connect_as(cluster, name, "pipeshub_jobs") as c:
        for org in (ORG_A, ORG_B):
            with uow(c, "pipeshub_jobs", org):
                c.execute("INSERT INTO retrieval.group_members VALUES (%s, 1, 'g', 4, 'p1'), (%s, 1, 'g', 5, 's1')", (org, org))

    def members() -> list[tuple]:
        with cluster.admin(name) as a:                                  # the superuser sees every org
            return [tuple(r) for r in a.execute("SELECT org_id, kind FROM retrieval.group_members ORDER BY 1, 2")]

    assert members() == [(o, k) for o in sorted((ORG_A, ORG_B)) for k in (1, 4, 5)]
    r = run_migrate(cluster.url(name, "pipeshub_migrate"), "downgrade", "retrieval_0001", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    assert members() == [(o, 1) for o in sorted((ORG_A, ORG_B))]

    def forced() -> bool:
        with cluster.admin(name) as a:
            return a.execute("SELECT relforcerowsecurity FROM pg_class WHERE oid = 'retrieval.group_members'::regclass").fetchone()[0]

    assert forced()                     # the delete lifted the force only for its own statement
    with cluster.admin(name) as a:
        assert a.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema = 'retrieval' AND table_name = 'point_groups' AND column_name = 'scope_stamp'").fetchone()[0] == 0
        with pytest.raises(pg.CheckViolation):
            a.execute("INSERT INTO retrieval.group_members VALUES (%s, 1, 'g', 4, 'p1')", (ORG_A,))
    r = run_migrate(cluster.url(name, "pipeshub_migrate"))
    assert r.returncode == 0, r.stdout
    assert forced()
    with cluster.admin(name) as a:
        a.execute("INSERT INTO retrieval.group_members VALUES (%s, 1, 'g', 4, 'p1'), (%s, 1, 'g', 5, 's1')", (ORG_A, ORG_B))
        assert a.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema = 'retrieval' AND table_name = 'point_groups' AND column_name = 'scope_stamp'").fetchone()[0] == 1


# ---- whether another org still holds a vrid (retrieval_0004) ------------------------------------------------------------------------------
HELD = "SELECT retrieval.vrid_held_elsewhere(%s)"
HELD_SIG = "retrieval.vrid_held_elsewhere(text)"


def put_vrid(c: psycopg.Connection, org: str, vrid: str, tombstone: bool = False) -> None:
    sk = c.execute("INSERT INTO retrieval.points (org_id, collection_id, space_id, point_id, group_key, vrid, embedding, embedding_dim) "
                   "VALUES (%s, 1, 1, gen_random_uuid(), %s, %s, %s::ext.halfvec, 4) RETURNING point_sk", (org, vrid, vrid, VEC)).fetchone()[0]
    if tombstone:
        c.execute("INSERT INTO retrieval.point_tombstones (org_id, point_sk) VALUES (%s, %s)", (org, sk))


def held(cluster, name: str, org: str | None, vrid: str) -> bool:
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", org):
        return c.execute(HELD, (vrid,)).fetchone()[0]


def test_vrid_held_elsewhere_answers_for_the_other_orgs_only(rdb):
    """A and B both hold vr-shared; only A holds vr-a; B's only point of vr-gone is tombstoned (deleted in safe mode)."""
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    adopt(cluster, name, ORG_B)
    with connect_as(cluster, name, "pipeshub_jobs") as c:
        with uow(c, "pipeshub_jobs", ORG_A):
            put_vrid(c, ORG_A, "vr-shared")
            put_vrid(c, ORG_A, "vr-a")
            put_vrid(c, ORG_A, "vr-gone")
        with uow(c, "pipeshub_jobs", ORG_B):
            put_vrid(c, ORG_B, "vr-shared")
            put_vrid(c, ORG_B, "vr-gone", tombstone=True)
    assert held(cluster, name, ORG_A, "vr-shared") is True
    assert held(cluster, name, ORG_B, "vr-shared") is True
    assert held(cluster, name, ORG_A, "vr-a") is False               # the caller's own points do not count
    assert held(cluster, name, ORG_B, "vr-a") is True
    assert held(cluster, name, ORG_A, "vr-gone") is False            # a tombstoned point is not held
    assert held(cluster, name, ORG_A, "vr-none") is False
    with connect_as(cluster, name, "pipeshub_jobs") as c, uow(c, "pipeshub_jobs", ORG_B):
        c.execute("DELETE FROM retrieval.points WHERE org_id = %s AND vrid = 'vr-shared'", (ORG_B,))
    assert held(cluster, name, ORG_A, "vr-shared") is False          # the last other copy went
    assert held(cluster, name, None, "vr-a") is True                 # no org: the answer that keeps the mapping, whatever is stored


def test_vrid_held_elsewhere_returns_one_boolean_and_nothing_else(rdb):
    cluster, name = rdb
    with cluster.admin(name) as a:
        row = a.execute("SELECT prorettype::regtype::text, proretset, proallargtypes, pg_get_function_arguments(oid) FROM pg_proc "
                        "WHERE oid = %s::regprocedure", (HELD_SIG,)).fetchone()
    assert row == ("boolean", False, None, "p_vrid text")              # one scalar boolean, no OUT argument, no set
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        cur = c.execute(HELD, ("vr-x",))
        assert [d.name for d in cur.description] == ["vrid_held_elsewhere"] and cur.fetchall() == [(False,)]
    for bad in ("", None, "v" * 2001):
        with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A), pytest.raises(pg.InvalidParameterValue):
            c.execute(HELD, (bad,))


def test_vrid_held_elsewhere_is_executable_by_the_indexing_role_only(rdb):
    cluster, name = rdb
    with cluster.admin(name) as a:
        for role, ok in (("pipeshub_indexing", True), ("pipeshub_node", False), ("pipeshub_connectors", False), ("pipeshub_query", False),
                         ("pipeshub_jobs", False), ("pipeshub_kv", False), ("pipeshub_backup", False), ("pipeshub_restore", False), ("public", False)):
            assert a.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, HELD_SIG)).fetchone()[0] is ok, role
        definer, config = a.execute("SELECT prosecdef, proconfig FROM pg_proc WHERE oid = %s::regprocedure", (HELD_SIG,)).fetchone()
    assert definer and "search_path=pg_catalog, retrieval, pg_temp" in config
    for login, role in (("pipeshub_query_login", "pipeshub_query"), ("pipeshub_connectors_login", "pipeshub_connectors"), ("pipeshub_node_login", "pipeshub_node")):
        with connect_as(cluster, name, login) as c, uow(c, role, ORG_A), pytest.raises(pg.InsufficientPrivilege):
            c.execute(HELD, ("vr-a",))


def test_vrid_held_elsewhere_counts_a_point_in_a_space_that_is_not_active(rdb):
    """Org B holds its only point of the vrid in a shadow space and has no active space: it still holds the vrid."""
    cluster, name = rdb
    with cluster.admin(name) as a:
        a.execute("INSERT INTO retrieval.embedding_spaces (space_id, model, dim, metric) OVERRIDING SYSTEM VALUE VALUES (30005, 'shadow-model', 4, 'cosine') "
                  "ON CONFLICT DO NOTHING")
        a.execute("INSERT INTO retrieval.org_spaces (org_id, space_id, dim, role) VALUES (%s, 30005, 4, 'shadow')", (ORG_B,))
        a.execute("INSERT INTO retrieval.points (org_id, collection_id, space_id, point_id, group_key, vrid, embedding, embedding_dim) "
                  "VALUES (%s, 1, 30005, gen_random_uuid(), 'vr-shadow', 'vr-shadow', %s::ext.halfvec, 4)", (ORG_B, VEC))
        assert a.execute("SELECT count(*) FROM retrieval.org_spaces WHERE org_id = %s AND role = 'active'", (ORG_B,)).fetchone()[0] == 0
    assert held(cluster, name, ORG_A, "vr-shadow") is True


def test_vrid_held_elsewhere_probes_the_vrid_index_and_never_scans_the_points(rdb):
    """20,000 points in each of two orgs, half of B's tombstoned. Seven calls: the plpgsql plan cache goes generic after five."""
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    adopt(cluster, name, ORG_B)
    with cluster.admin(name) as c:
        for org in (ORG_A, ORG_B):
            c.execute("INSERT INTO retrieval.points (org_id, collection_id, space_id, point_id, group_key, vrid, embedding, embedding_dim) "
                      "SELECT %s, 1, 1, gen_random_uuid(), 'vr-' || g, 'vr-' || g, %s::ext.halfvec, 4 FROM generate_series(1, 20000) g", (org, VEC))
        c.execute("INSERT INTO retrieval.point_tombstones (org_id, point_sk) SELECT org_id, point_sk FROM retrieval.points WHERE org_id = %s AND point_sk %% 2 = 0", (ORG_B,))
        c.execute("ANALYZE retrieval.points, retrieval.point_tombstones")
        big = vetted_harness.org_tables_over(c, 1000)
        plans = vetted_harness.plans_auto_explain(c, "pipeshub_indexing", ORG_A, HELD, ("vr-777",))
    assert any(t.startswith("points_") for t in big) and any(t.startswith("point_tombstones_") for t in big), big
    # an empty leaf may be read whole; a leaf that holds rows is probed: points by (org, collection, vrid), a tombstone by its key
    for i in (0, 6):
        assert "_vrid_block_id_idx" in plans[i] and "Scan using point_tombstones_h" in plans[i], (i, plans[i])
        assert vetted_harness.seq_scanned(plans[i], big) == [], (i, plans[i])


# ---- the membership keys on the point row (retrieval_0006) ------------------------------------------------------------------------

def keys_of(cluster, name, org: str) -> dict[str, list[str] | None]:
    with cluster.admin(name) as c:
        return dict(c.execute("SELECT point_id::text, member_keys FROM retrieval.points WHERE org_id = %s ORDER BY 1", (org,)))


def group(c, org: str, key: str, members: dict[int, list[str]]) -> None:
    """A group row and its membership rows, as write_groups leaves them (the row first, then the member diff)."""
    c.execute("INSERT INTO retrieval.point_groups (org_id, collection_id, group_key, computed_at) VALUES (%s, 1, %s, clock_timestamp()) "
              "ON CONFLICT (org_id, collection_id, group_key) DO UPDATE SET computed_at = EXCLUDED.computed_at", (org, key))
    c.execute("DELETE FROM retrieval.group_members WHERE org_id = %s AND collection_id = 1 AND group_key = %s AND kind IN (1, 2, 3)", (org, key))
    for kind, ids in members.items():
        c.execute("INSERT INTO retrieval.group_members (org_id, collection_id, group_key, kind, member_id) SELECT %s, 1, %s, %s, unnest(%s::text[])",
                  (org, key, kind, ids))


P1, P2, P3 = (f"a0000000-0000-0000-0000-00000000010{i}" for i in (1, 2, 3))


def test_a_point_row_carries_its_groups_membership_keys_whoever_writes_and_in_whatever_order(rdb):
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    with connect_as(cluster, name, "pipeshub_indexing_login") as c:
        with uow(c, "pipeshub_indexing", ORG_A):
            group(c, ORG_A, "g1", {1: ["c1"], 2: ["rg2", "rg1"]})
            put(c, ORG_A, P1)                                                   # group_key = point id: no group row
            c.execute("UPDATE retrieval.points SET group_key = 'g1' WHERE org_id = %s AND point_id = %s", (ORG_A, P1))   # moved into g1
            c.execute("INSERT INTO retrieval.points (org_id, collection_id, space_id, point_id, group_key, embedding, embedding_dim) "
                      "VALUES (%s, 1, 1, %s, 'g1', %s::ext.halfvec, 4), (%s, 1, 1, %s, 'g2', %s::ext.halfvec, 4)", (ORG_A, P2, VEC, ORG_A, P3, VEC))
    assert keys_of(cluster, name, ORG_A) == {P1: ["1:c1", "2:rg1", "2:rg2"], P2: ["1:c1", "2:rg1", "2:rg2"], P3: None}   # g2 has no row: unknown
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        group(c, ORG_A, "g1", {1: ["c2"], 3: ["root"]})                         # the membership changes after the points were written
        group(c, ORG_A, "g2", {})
        c.execute("INSERT INTO retrieval.group_members (org_id, collection_id, group_key, kind, member_id) VALUES (%s, 1, 'g1', 4, 'path')", (ORG_A,))
    assert keys_of(cluster, name, ORG_A) == {P1: ["1:c2", "3:root"], P2: ["1:c2", "3:root"], P3: None}     # kind 4 is not an arm; g2 has no members
    with connect_as(cluster, name, "pipeshub_jobs") as c, uow(c, "pipeshub_jobs", ORG_A):
        c.execute("DELETE FROM retrieval.group_members WHERE org_id = %s AND group_key = 'g1' AND kind = 1", (ORG_A,))   # a job removes members
    assert keys_of(cluster, name, ORG_A)[P1] == ["3:root"]


def test_a_point_and_a_membership_change_of_its_group_wait_for_each_other(rdb):
    """The keys a point takes are never those of a membership another transaction is changing: the two writers are serialised on the
    group row (FOR SHARE against the membership writer's update), so the later one sees and keeps the earlier one's state."""
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        group(c, ORG_A, "g1", {1: ["c1"]})
    with connect_as(cluster, name, "pipeshub_indexing_login") as members, connect_as(cluster, name, "pipeshub_indexing_login") as points:
        with uow(members, "pipeshub_indexing", ORG_A):
            group(members, ORG_A, "g1", {1: ["c2"]})                            # uncommitted
            with pytest.raises(pg.LockNotAvailable), uow(points, "pipeshub_indexing", ORG_A):
                points.execute("SET LOCAL lock_timeout = '300ms'")
                points.execute("INSERT INTO retrieval.points (org_id, collection_id, space_id, point_id, group_key, embedding, embedding_dim) "
                               "VALUES (%s, 1, 1, %s, 'g1', %s::ext.halfvec, 4)", (ORG_A, P1, VEC))
        with uow(points, "pipeshub_indexing", ORG_A):                           # after the membership committed: its keys
            points.execute("INSERT INTO retrieval.points (org_id, collection_id, space_id, point_id, group_key, embedding, embedding_dim) "
                           "VALUES (%s, 1, 1, %s, 'g1', %s::ext.halfvec, 4)", (ORG_A, P1, VEC))
            with pytest.raises(pg.LockNotAvailable), uow(members, "pipeshub_indexing", ORG_A):     # and the other way round
                members.execute("SET LOCAL lock_timeout = '300ms'")
                group(members, ORG_A, "g1", {1: ["c3"]})
        with uow(members, "pipeshub_indexing", ORG_A):
            group(members, ORG_A, "g1", {1: ["c3"]})
    assert keys_of(cluster, name, ORG_A) == {P1: ["1:c3"]}


def test_a_membership_change_by_any_writer_waits_for_an_open_point_of_its_group_and_then_rewrites_its_keys(rdb):
    """A job that deletes membership rows without touching the group row still waits for a point writer of that group (the membership
    triggers take the group rows), so the point it had not seen when it ran gets its keys rewritten instead of keeping the old ones."""
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        group(c, ORG_A, "g1", {1: ["c1"]})
    with connect_as(cluster, name, "pipeshub_indexing_login") as points, connect_as(cluster, name, "pipeshub_jobs") as jobs:
        with uow(points, "pipeshub_indexing", ORG_A):
            points.execute("INSERT INTO retrieval.points (org_id, collection_id, space_id, point_id, group_key, embedding, embedding_dim) "
                           "VALUES (%s, 1, 1, %s, 'g1', %s::ext.halfvec, 4)", (ORG_A, P1, VEC))           # uncommitted, keys 1:c1
            with pytest.raises(pg.LockNotAvailable), uow(jobs, "pipeshub_jobs", ORG_A):
                jobs.execute("SET LOCAL lock_timeout = '300ms'")
                jobs.execute("DELETE FROM retrieval.group_members WHERE org_id = %s AND group_key = 'g1' AND kind = 1", (ORG_A,))
        with uow(jobs, "pipeshub_jobs", ORG_A):
            jobs.execute("DELETE FROM retrieval.group_members WHERE org_id = %s AND group_key = 'g1' AND kind = 1", (ORG_A,))
    assert keys_of(cluster, name, ORG_A) == {P1: []}
    with connect_as(cluster, name, "pipeshub_jobs") as jobs, uow(jobs, "pipeshub_jobs", ORG_A):           # a group without a row: unknown keys
        jobs.execute("DELETE FROM retrieval.point_groups WHERE org_id = %s AND group_key = 'g1'", (ORG_A,))
        jobs.execute("INSERT INTO retrieval.group_members VALUES (%s, 1, 'g1', 1, 'c9')", (ORG_A,))
    assert keys_of(cluster, name, ORG_A) == {P1: None}


def test_a_point_moved_to_another_collection_takes_that_collections_membership_keys(rdb):
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        group(c, ORG_A, "g1", {1: ["c1"]})
        c.execute("INSERT INTO retrieval.point_groups (org_id, collection_id, group_key, computed_at) VALUES (%s, 2, 'g1', clock_timestamp())", (ORG_A,))
        c.execute("INSERT INTO retrieval.group_members VALUES (%s, 2, 'g1', 1, 'c2')", (ORG_A,))
        c.execute("INSERT INTO retrieval.points (org_id, collection_id, space_id, point_id, group_key, embedding, embedding_dim) "
                  "VALUES (%s, 1, 1, %s, 'g1', %s::ext.halfvec, 4)", (ORG_A, P1, VEC))
    assert keys_of(cluster, name, ORG_A) == {P1: ["1:c1"]}
    with connect_as(cluster, name, "pipeshub_jobs") as c, uow(c, "pipeshub_jobs", ORG_A):
        c.execute("UPDATE retrieval.points SET collection_id = 2 WHERE org_id = %s AND point_id = %s", (ORG_A, P1))
    assert keys_of(cluster, name, ORG_A) == {P1: ["1:c2"]}


def test_point_writers_and_membership_writers_running_at_once_leave_every_points_keys_equal_to_its_groups_membership(rdb):
    """Point inserts and membership changes of one group, interleaved by two connections for a while (the membership writer a job that
    never touches the group row itself): afterwards every point's keys are its group's membership, none kept from a state since replaced.
    The point writer starts only once the membership writer is connected, and at least one membership change must commit while points
    are still being written, so the test cannot pass on point writes alone."""
    import threading

    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        group(c, ORG_A, "g1", {1: ["c0"]})
    stop, ready, errors, overlapped = threading.Event(), threading.Event(), [], []

    def points() -> None:
        try:
            with connect_as(cluster, name, "pipeshub_indexing_login") as c:
                if not ready.wait(20):
                    errors.append("the membership writer never started")
                    return
                for _ in range(60):
                    with uow(c, "pipeshub_indexing", ORG_A):
                        c.execute("SET LOCAL lock_timeout = '20s'")                     # a wait that never ends fails the test instead of hanging it
                        c.execute("INSERT INTO retrieval.points (org_id, collection_id, space_id, point_id, group_key, embedding, embedding_dim) "
                                  "VALUES (%s, 1, 1, gen_random_uuid(), 'g1', %s::ext.halfvec, 4)", (ORG_A, VEC))
                        c.execute("SELECT pg_sleep(0.005)")                               # hold the group row while the other side runs
        except psycopg.Error as e:
            errors.append(e)
        finally:
            stop.set()

    def members() -> None:
        with connect_as(cluster, name, "pipeshub_jobs") as c:
            ready.set()
            i = 0
            while not stop.is_set():
                i += 1
                try:
                    with uow(c, "pipeshub_jobs", ORG_A):
                        c.execute("SET LOCAL lock_timeout = '20s'")
                        c.execute("DELETE FROM retrieval.group_members WHERE org_id = %s AND group_key = 'g1' AND kind = 1", (ORG_A,))
                        c.execute("INSERT INTO retrieval.group_members VALUES (%s, 1, 'g1', 1, %s)", (ORG_A, f"c{i}"))
                    if not stop.is_set():
                        overlapped.append(i)                                              # committed while points were still being written
                except psycopg.Error as e:                                                # a deadlock or a lock wait that ran out
                    errors.append(e)

    threads = [threading.Thread(target=points, daemon=True), threading.Thread(target=members, daemon=True)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    assert not any(t.is_alive() for t in threads) and errors == []
    assert overlapped
    with cluster.admin(name) as c:
        want = c.execute("SELECT array_agg(kind || ':' || member_id ORDER BY kind, member_id) FROM retrieval.group_members "
                         "WHERE org_id = %s AND group_key = 'g1' AND kind IN (1, 2, 3)", (ORG_A,)).fetchone()[0]
        got = {tuple(r[0]) for r in c.execute("SELECT member_keys FROM retrieval.points WHERE org_id = %s", (ORG_A,))}
    assert got == {tuple(want)}


def test_a_restored_point_is_searched_through_the_membership_rows_whatever_keys_its_export_carried(rdb):
    """A restore loads exported rows, and the export of points and of membership rows need not be one snapshot: the keys a restored
    point carries could be out of date, so it gets none (NULL: the search probes the membership rows) until its group is written again."""
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    with cluster.admin(name) as c:
        with c.transaction():
            c.execute("INSERT INTO retrieval.point_groups (org_id, collection_id, group_key, computed_at) VALUES (%s, 1, 'g1', now())", (ORG_A,))
            c.execute("INSERT INTO kernel.restore_window (org, until) VALUES (%s, now() + interval '1 hour')", (ORG_A,))
            c.execute("SET LOCAL ROLE pipeshub_restore")
            c.execute("INSERT INTO retrieval.points (org_id, collection_id, space_id, point_id, group_key, embedding, embedding_dim, member_keys) "
                      "VALUES (%s, 1, 1, %s, 'g1', %s, 4, '{1:exported}')", (ORG_A, P1, VEC))             # as a load does: text into the column
            c.execute("RESET ROLE")
            c.execute("DELETE FROM kernel.restore_window")
    assert keys_of(cluster, name, ORG_A) == {P1: None}


def test_a_membership_change_rewrites_the_keys_of_its_groups_points_by_index_without_reading_the_orgs_other_points(rdb):
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        group(c, ORG_A, "g1", {1: ["c1"]})
        c.execute("INSERT INTO retrieval.points (org_id, collection_id, space_id, point_id, group_key, embedding, embedding_dim) "
                  "SELECT %s, 1, 1, gen_random_uuid(), 'other' || g, %s::ext.halfvec, 4 FROM generate_series(1, 3000) g", (ORG_A, VEC))
        c.execute("INSERT INTO retrieval.points (org_id, collection_id, space_id, point_id, group_key, embedding, embedding_dim) "
                  "VALUES (%s, 1, 1, %s, 'g1', %s::ext.halfvec, 4)", (ORG_A, P1, VEC))
    with cluster.admin(name) as c:
        c.execute("ANALYZE retrieval.points")
    with connect_as(cluster, name, "pipeshub_jobs") as c, uow(c, "pipeshub_jobs", ORG_A):
        c.execute("INSERT INTO retrieval.group_members VALUES (%s, 1, 'g1', 2, 'rg1')", (ORG_A,))
        seq = c.execute("SELECT coalesce(sum(pg_stat_get_xact_numscans(c.oid)), 0)::int FROM pg_class c "
                        "WHERE c.relnamespace = 'retrieval'::regnamespace AND c.relkind = 'r' AND c.relname LIKE 'points%%'").fetchone()[0]
    assert seq == 0                                                                    # no sequential scan of the points; their group index instead
    assert keys_of(cluster, name, ORG_A)[P1] == ["1:c1", "2:rg1"]
