"""The retrieval module: tables, the default tree, row-level security on points, the jobs-only catalog writes, the lexical search function
and the space checks. The generic checks of the vetted functions are in test_vetted.py (its manifest list includes this module's manifest)."""


import psycopg
import pytest
from psycopg import errors as pg

from .conftest import run_migrate
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


def test_every_table_the_lexical_search_reads_is_filtered_by_the_bound_org():
    """Static check: the function takes the org from the unit of work, so every reference to a retrieval table must say so."""
    import re
    from pathlib import Path

    from db.lib import vetted

    fn = next(f for f in vetted.load(Path(__file__).parents[1] / "versions/retrieval/vetted/manifest.yaml") if f.name == "search_sparse")
    refs = re.findall(r"\b(?:FROM|JOIN)\s+retrieval\.(\w+)\s+(\w+)", fn.body)
    assert {t for t, _ in refs} == {"org_spaces", "collection_stats", "lexical_df_delta", "postings", "points", "group_members", "point_tombstones"}
    for table, alias in refs:
        assert re.search(rf"\b{alias}\.org_id = v_org\b", fn.body), f"{table} {alias} is not filtered by v_org"
    assert "p_org" not in fn.body and all("org" not in a.name for a in fn.args)               # no argument can name an org


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
        assert {r[0] for r in c.execute("SELECT class_id FROM kernel.lock_keys")} >= {7361, 7362, 7363, 7370}


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
    with cluster.admin(name) as a:
        assert a.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema = 'retrieval' AND table_name = 'point_groups' AND column_name = 'scope_stamp'").fetchone()[0] == 0
        with pytest.raises(pg.CheckViolation):
            a.execute("INSERT INTO retrieval.group_members VALUES (%s, 1, 'g', 4, 'p1')", (ORG_A,))
    r = run_migrate(cluster.url(name, "pipeshub_migrate"))
    assert r.returncode == 0, r.stdout
    with cluster.admin(name) as a:
        a.execute("INSERT INTO retrieval.group_members VALUES (%s, 1, 'g', 4, 'p1'), (%s, 1, 'g', 5, 's1')", (ORG_A, ORG_B))
        assert a.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema = 'retrieval' AND table_name = 'point_groups' AND column_name = 'scope_stamp'").fetchone()[0] == 1
