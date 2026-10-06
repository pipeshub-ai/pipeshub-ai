"""``list_all_records`` against a real Neo4j and a real ArangoDB.

Requires: your own throwaway Neo4j / ArangoDB and PCC_* env; see README.md in this folder.
Run: pytest tests/integration/graph_db/test_list_all_records_parity.py -m integration

The listing is what a user sees of their knowledge, so it must equal what they
are authorised to read and must not depend on the backend. One graph is seeded
per test: a KB the user holds directly, a KB reachable only through a team, KBs
reachable both ways (direct READER + team WRITER, direct WRITER + team READER), a KB with no access, plus a connector record.
"""

import uuid

import pytest

from ._backends import unavailable

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

ORG = "org-it"

_DOC_COLLECTIONS = ("users", "records", "apps", "teams")
_EDGE_COLLECTIONS = ("permission", "belongsTo", "isOfType")
_NEO4J_LABELS = {"users": "User", "records": "Record", "apps": "App", "teams": "Teams"}
_NEO4J_EDGES = {"permission": "PERMISSION", "belongsTo": "BELONGS_TO"}


def _log():
    from app.utils.logger import create_logger
    return create_logger("list_all_records_parity_test")


@pytest.fixture(scope="module")
async def neo4j_provider(neo4j_env):
    pytest.importorskip("neo4j", reason="neo4j driver not installed")
    from app.services.graph_db.neo4j.neo4j_client import Neo4jClient
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

    uri = neo4j_env.uri
    password = neo4j_env.password
    logger = _log()
    client = Neo4jClient(
        uri=uri, username=neo4j_env.user, password=password, database="neo4j", logger=logger
    )
    try:
        if not await client.connect():
            unavailable(f"Neo4j not available at {uri}")
    except Exception as exc:
        unavailable(f"Neo4j not available at {uri} — {exc}")

    provider = Neo4jProvider.__new__(Neo4jProvider)
    provider.logger = logger
    provider.client = client
    yield provider
    await client.disconnect()


@pytest.fixture(scope="module")
async def arango_provider(arango_env):
    pytest.importorskip("aiohttp", reason="aiohttp not installed")
    import aiohttp

    from app.services.graph_db.arango.arango_http_client import ArangoHTTPClient
    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

    url = arango_env.url
    password = arango_env.password
    db = arango_env.db_or("es")
    logger = _log()
    client = ArangoHTTPClient(
        base_url=url, username=arango_env.user, password=password, database=db, logger=logger
    )
    try:
        async with aiohttp.ClientSession(auth=aiohttp.BasicAuth("root", password)) as s:
            async with s.post(f"{url}/_db/_system/_api/database", json={"name": db}) as r:
                if r.status not in (200, 201, 409):
                    raise RuntimeError(f"cannot create database {db}: {r.status}")
            wanted = [(n, 2) for n in _DOC_COLLECTIONS] + [(n, 3) for n in _EDGE_COLLECTIONS]
            for name, kind in wanted:
                async with s.post(
                    f"{url}/_db/{db}/_api/collection", json={"name": name, "type": kind}
                ) as r:
                    if r.status not in (200, 201, 409):
                        raise RuntimeError(f"cannot create collection {name}: {r.status}")
    except Exception as exc:
        unavailable(f"ArangoDB not available at {url} — {exc}")

    provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    provider.logger = logger
    provider.http_client = client
    yield provider
    await client.disconnect()


class _Graph:
    def __init__(self) -> None:
        run = uuid.uuid4().hex[:8]
        self.run = run
        self.user = f"{run}-u"
        self.nodes: list[tuple[str, str, dict]] = []
        self.edges: list[tuple[str, tuple[str, str], tuple[str, str], dict]] = []

        kb = {name: f"{run}-kb-{name}" for name in ("direct", "team", "both", "up", "none", "legacy", "stamped")}
        team = f"{run}-team"
        team2 = f"{run}-team2"
        team3 = f"{run}-team3"
        team4 = f"{run}-team4"
        self.nodes.append(("users", self.user, {"userId": f"{run}-ext", "orgId": ORG}))
        self.nodes += [("teams", t, {"orgId": ORG}) for t in (team, team2, team3, team4)]
        for name, kid in kb.items():
            self.nodes.append(("apps", kid, {"type": "KB", "orgId": ORG, "name": name}))

        self._perm(("users", self.user), ("apps", kb["direct"]), "USER", "OWNER")
        self._perm(("users", self.user), ("apps", kb["both"]), "USER", "WRITER")
        self._perm(("users", self.user), ("teams", team), "USER", "READER")
        self._perm(("users", self.user), ("teams", team2), "USER", "WRITER")
        self._perm(("users", self.user), ("teams", team3), "USER", "OWNER")
        self._perm(("users", self.user), ("teams", team4), "USER", "WRITER")
        # Legacy team->KB edges carry no role: the member's team role applies, capped at WRITER.
        self._perm(("teams", team), ("apps", kb["team"]), "TEAM", None)
        self._perm(("teams", team2), ("apps", kb["team"]), "TEAM", None)
        self._perm(("teams", team), ("apps", kb["both"]), "TEAM", None)
        self._perm(("users", self.user), ("apps", kb["up"]), "USER", "READER")
        self._perm(("teams", team2), ("apps", kb["up"]), "TEAM", None)
        self._perm(("teams", team3), ("apps", kb["legacy"]), "TEAM", None)
        # A stamped edge role is a grant to the whole team and wins over the member's role.
        self._perm(("teams", team4), ("apps", kb["stamped"]), "TEAM", "READER")

        # Names sort in this order, so pagination windows are deterministic.
        self.direct = [self._kb_record(f"{run}-a1", kb["direct"], recordType="FILE"),
                       self._kb_record(f"{run}-a2", kb["direct"], recordType="WEBPAGE",
                                       indexingStatus="FAILED")]
        self.team_only = [self._kb_record(f"{run}-b1", kb["team"]),
                          self._kb_record(f"{run}-b2", kb["team"], indexingStatus="FAILED")]
        self.both = [self._kb_record(f"{run}-c1", kb["both"])]
        self.up = [self._kb_record(f"{run}-c2", kb["up"])]
        self.legacy = [self._kb_record(f"{run}-f1", kb["legacy"])]
        self.stamped = [self._kb_record(f"{run}-f2", kb["stamped"])]
        self.denied = [self._kb_record(f"{run}-d1", kb["none"])]
        self._kb_record(f"{run}-gone", kb["direct"], isDeleted=True)
        self.connector = self._record(f"{run}-e1", origin="CONNECTOR", connectorName="JIRA")
        self._perm(("users", self.user), ("records", self.connector), "USER", "READER")

    def _perm(self, frm, to, kind, role) -> None:
        props = {"type": kind} if role is None else {"type": kind, "role": role}
        self.edges.append(("permission", frm, to, props))

    def _record(self, rid, **props) -> str:
        base = {"orgId": ORG, "recordName": rid, "isDeleted": False, "isFile": True,
                "mimeType": "text/plain", "recordType": "FILE", "origin": "UPLOAD",
                "indexingStatus": "COMPLETED", "createdAtTimestamp": 1000}
        base.update(props)
        self.nodes.append(("records", rid, base))
        return rid

    def _kb_record(self, rid, kb_id, **props) -> str:
        self._record(rid, **props)
        self.edges.append(("belongsTo", ("records", rid), ("apps", kb_id), {}))
        return rid

    @property
    def kb_visible(self) -> set[str]:
        return {*self.direct, *self.team_only, *self.both, *self.up, *self.legacy, *self.stamped}

    @property
    def authorised(self) -> set[str]:
        return {*self.kb_visible, self.connector}


async def _seed_neo4j(provider, g: _Graph) -> None:
    for coll, key, props in g.nodes:
        await provider.client.execute_query(
            f"CREATE (n:{_NEO4J_LABELS[coll]}) SET n = $props",
            parameters={"props": {**props, "id": key, "itRun": g.run}},
        )
    for coll, (fc, fk), (tc, tk), props in g.edges:
        await provider.client.execute_query(
            f"MATCH (a:{_NEO4J_LABELS[fc]} {{id: $f}}), (b:{_NEO4J_LABELS[tc]} {{id: $t}}) "
            f"CREATE (a)-[r:{_NEO4J_EDGES[coll]}]->(b) SET r = $props",
            parameters={"f": fk, "t": tk, "props": props},
        )


async def _clean_neo4j(provider, g: _Graph) -> None:
    await provider.client.execute_query(
        "MATCH (n {itRun: $r}) DETACH DELETE n", parameters={"r": g.run}
    )


async def _seed_arango(provider, g: _Graph) -> None:
    for coll, key, props in g.nodes:
        await provider.http_client.execute_aql(
            f"INSERT MERGE(@props, {{_key: @k, itRun: @r}}) INTO {coll}",
            bind_vars={"props": props, "k": key, "r": g.run},
        )
    for coll, (fc, fk), (tc, tk), props in g.edges:
        await provider.http_client.execute_aql(
            f"INSERT MERGE(@props, {{_from: @f, _to: @t, itRun: @r}}) INTO {coll}",
            bind_vars={"props": props, "f": f"{fc}/{fk}", "t": f"{tc}/{tk}", "r": g.run},
        )


async def _clean_arango(provider, g: _Graph) -> None:
    for coll in _DOC_COLLECTIONS + _EDGE_COLLECTIONS:
        await provider.http_client.execute_aql(
            f"FOR d IN {coll} FILTER d.itRun == @r REMOVE d IN {coll}",
            bind_vars={"r": g.run},
        )


async def _roles(provider, g: _Graph, **kw) -> dict[str, str]:
    args = dict(
        user_id=g.user, org_id=ORG, skip=0, limit=50, search=None, record_types=None,
        origins=None, connectors=None, indexing_status=None, permissions=None,
        date_from=None, date_to=None, sort_by="recordName", sort_order="asc", source="local",
    )
    args.update(kw)
    records, _, _ = await provider.list_all_records(**args)
    return {r["id"]: r["permission"]["role"] for r in records}


async def _list(provider, g: _Graph, **kw):
    args = dict(
        user_id=g.user, org_id=ORG, skip=0, limit=50, search=None, record_types=None,
        origins=None, connectors=None, indexing_status=None, permissions=None,
        date_from=None, date_to=None, sort_by="recordName", sort_order="asc", source="all",
    )
    args.update(kw)
    records, total, _ = await provider.list_all_records(**args)
    return [r["id"] for r in records], total


class _Contract:
    seed = None
    clean = None

    async def _with_graph(self, provider, check) -> None:
        g = _Graph()
        await type(self).seed(provider, g)
        try:
            await check(provider, g)
        finally:
            await type(self).clean(provider, g)

    async def test_listing_equals_authorised_set_without_duplicates(self, provider):
        async def check(provider, g):
            ids, total = await _list(provider, g)
            assert sorted(ids) == sorted(g.authorised)
            assert len(ids) == len(set(ids))
            assert total == len(g.authorised)
        await self._with_graph(provider, check)

    async def test_local_source_lists_only_kb_records(self, provider):
        async def check(provider, g):
            ids, total = await _list(provider, g, source="local")
            assert set(ids) == g.kb_visible
            assert total == len(ids)
        await self._with_graph(provider, check)

    async def test_kb_role_is_highest_across_direct_and_team_paths(self, provider):
        async def check(provider, g):
            roles = await _roles(provider, g)
            assert roles[g.up[0]] == "WRITER"  # direct READER, team WRITER
            assert roles[g.both[0]] == "WRITER"  # direct WRITER, team READER
            assert roles[g.direct[0]] == "OWNER"
            assert roles[g.team_only[0]] == "WRITER"  # via team2 (WRITER) over team (READER)
            assert roles[g.legacy[0]] == "WRITER"  # role-less team edge, team OWNER member: capped
            assert roles[g.stamped[0]] == "READER"  # stamped edge role beats a WRITER member
        await self._with_graph(provider, check)

    async def test_permissions_filter_applies_to_the_effective_role(self, provider):
        async def check(provider, g):
            roles = await _roles(provider, g, permissions=["READER"])
            assert set(roles) == set(g.stamped)  # direct READER on `up` is outranked by team WRITER
            assert set(roles.values()) == {"READER"}
            roles = await _roles(provider, g, permissions=["WRITER"])
            assert set(roles) == {*g.team_only, *g.both, *g.up, *g.legacy}
            assert set(roles.values()) == {"WRITER"}
            ids, total = await _list(provider, g, permissions=["WRITER"], source="local")
            assert total == len(ids) == 5
            roles = await _roles(provider, g, permissions=["OWNER"])
            assert set(roles) == set(g.direct)
        await self._with_graph(provider, check)

    async def test_filters_apply_to_list_and_count(self, provider):
        async def check(provider, g):
            ids, total = await _list(provider, g, indexing_status=["FAILED"], source="local")
            assert set(ids) == {g.direct[1], g.team_only[1]}
            assert total == 2
            ids, total = await _list(provider, g, search="-b", source="local")
            assert set(ids) == set(g.team_only)
            assert total == 2
            ids, total = await _list(provider, g, record_types=["WEBPAGE"], source="local")
            assert ids == [g.direct[1]]
            assert total == 1
        await self._with_graph(provider, check)

    async def test_pagination_windows_are_disjoint_and_cover_the_set(self, provider):
        async def check(provider, g):
            seen: list[str] = []
            for skip in range(0, len(g.authorised), 2):
                ids, total = await _list(provider, g, skip=skip, limit=2)
                assert total == len(g.authorised)
                seen += ids
            assert seen == sorted(g.authorised, key=lambda i: i)
        await self._with_graph(provider, check)


class TestNeo4jListAllRecords(_Contract):
    seed = staticmethod(_seed_neo4j)
    clean = staticmethod(_clean_neo4j)

    @pytest.fixture
    def provider(self, neo4j_provider):
        return neo4j_provider


class TestArangoListAllRecords(_Contract):
    seed = staticmethod(_seed_arango)
    clean = staticmethod(_clean_arango)

    @pytest.fixture
    def provider(self, arango_provider):
        return arango_provider
