"""The statements behind a full sync's tag-and-sweep, on both providers (FS-01).

Their behaviour on real databases is covered by
tests/integration/graph_permissions/test_write_path.py; these pin the shape of what
each provider sends, so neither drifts from the other.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.common.sync_sweep import PENDING_SWEEP
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

GENERATION = 1_760_000_000_000
NODES = {"all_node_ids": ["records/r1", "recordGroups/g1", "apps/c1"]}


@pytest.fixture
def arango() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(), MagicMock())
    provider.http_client = AsyncMock()
    provider._collect_connector_entities = AsyncMock(return_value=NODES)
    return provider


@pytest.fixture
def neo4j() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    provider._collect_connector_entities = AsyncMock(return_value=NODES)
    return provider


def _aql(provider: ArangoHTTPProvider) -> list[tuple[str, dict]]:
    return [(c.args[0], c.kwargs.get("bind_vars") or c.args[1]) for c in provider.http_client.execute_aql.await_args_list]


def _cypher(provider: Neo4jProvider) -> list[tuple[str, dict]]:
    return [(c.args[0], c.kwargs.get("parameters") or {}) for c in provider.client.execute_query.await_args_list]


class TestArango:
    @pytest.mark.asyncio
    async def test_mark_tags_every_sync_edge_of_the_connectors_nodes(self, arango) -> None:
        arango.http_client.execute_aql.return_value = [1, 1]

        marked, ok = await arango.mark_connector_sync_edges("c1", GENERATION)

        assert ok
        statements = _aql(arango)
        tagged = {bind["@c"] for _, bind in statements}
        assert {"permission", "userAppRelation", "inheritPermissions", "belongsTo",
                "nodeRelations", "recordLinks", "entityRelations"} <= tagged
        assert all(f"UPDATE edge WITH {{ {PENDING_SWEEP}: @generation }}" in q for q, _ in statements)
        assert all(bind["generation"] == GENERATION and bind["node_ids"] == NODES["all_node_ids"]
                   for _, bind in statements)
        assert marked == 2 * len(statements)

    @pytest.mark.asyncio
    async def test_sweep_removes_only_edges_still_carrying_this_tag(self, arango) -> None:
        arango.http_client.execute_aql.return_value = [1]

        deleted, ok = await arango.sweep_connector_sync_edges("c1", GENERATION)

        assert ok
        removes = [(q, b) for q, b in _aql(arango) if "REMOVE edge" in q]
        assert removes
        assert all(f"edge.{PENDING_SWEEP} == @generation" in q for q, _ in removes)
        assert deleted == len(removes)

    @pytest.mark.asyncio
    async def test_sweep_spares_the_collections_it_is_told_to_keep(self, arango) -> None:
        arango.http_client.execute_aql.return_value = [1]

        await arango.sweep_connector_sync_edges("c1", GENERATION, keep_collections=("userAppRelation",))

        removed = {b["@c"] for q, b in _aql(arango) if "REMOVE edge" in q}
        assert "userAppRelation" not in removed and "permission" in removed

    @pytest.mark.asyncio
    async def test_sweep_removes_nothing_when_the_sync_wrote_none_of_the_edges(self, arango) -> None:
        arango.http_client.execute_aql.return_value = [0]

        deleted, ok = await arango.sweep_connector_sync_edges("c1", GENERATION)

        assert (deleted, ok) == (0, False)
        assert not [q for q, _ in _aql(arango) if "REMOVE" in q]
        evidence = {b["@c"] for q, b in _aql(arango)}
        assert "userAppRelation" not in evidence, "a gate written before the source failed is no evidence"

    @pytest.mark.asyncio
    async def test_every_upsert_that_rewrites_an_edge_clears_the_tag(self, arango) -> None:
        arango.http_client.execute_aql.return_value = []
        edge = {"from_id": "u1", "from_collection": "users", "to_id": "r1", "to_collection": "records"}
        await arango.batch_create_edges([edge], "permission")
        await arango.batch_create_edges([{**edge, "relationshipType": "PARENT_CHILD"}], "nodeRelations")
        await arango.batch_create_edges([{**edge, "relationshipType": "BLOCKS"}], "nodeRelations")
        await arango.batch_create_entity_relations([{**edge, "edgeType": "ASSIGNED_TO"}])
        await arango.batch_upsert_node_relations([{**edge, "relationshipType": "FOREIGN_KEY"}])

        statements = [q for q, _ in _aql(arango)]
        assert len(statements) == 5
        # FVG-03: `pendingSweep: null` in an UPDATE stores the key; the rewrite must drop it.
        assert all('REPLACE UNSET(MERGE(OLD, edge), "_id", "_rev", "pendingSweep")' in q for q in statements)
        assert not any("pendingSweep: null" in q for q in statements)

    @pytest.mark.asyncio
    async def test_a_create_only_write_rewrites_a_tagged_edge_and_keeps_a_live_one(self, arango) -> None:
        arango.http_client.execute_aql.return_value = []
        await arango.ensure_app_membership("u1", "users", "c1", is_external=True)

        query, bind = _aql(arango)[0]
        assert "REPLACE OLD.pendingSweep == null OR OLD.pendingSweep NOT IN @running" in query
        assert 'UNSET(MERGE(OLD, @doc), "_id", "_rev", "pendingSweep")' in query
        assert bind["running"] == []

    @pytest.mark.asyncio
    async def test_clear_untags_this_generation_and_older_ones(self, arango) -> None:
        arango.http_client.execute_aql.return_value = [1]

        cleared, ok = await arango.clear_connector_sync_edge_tags("c1", GENERATION)

        assert ok
        statements = _aql(arango)
        assert {"permission", "userAppRelation", "belongsTo", "nodeRelations", "recordLinks"} <= {
            b["@c"] for _, b in statements}
        for query, bind in statements:
            assert f"edge.{PENDING_SWEEP} != null AND edge.{PENDING_SWEEP} <= @generation" in query
            assert f"UPDATE edge WITH {{ {PENDING_SWEEP}: null }} IN @@c OPTIONS {{ keepNull: false }}" in query
            assert bind["generation"] == GENERATION
        assert cleared == len(statements)


class TestNeo4j:
    @pytest.mark.asyncio
    async def test_mark_tags_every_sync_edge_of_each_label(self, neo4j) -> None:
        neo4j.client.execute_query.return_value = [{"edges": 4}]

        marked, ok = await neo4j.mark_connector_sync_edges("c1", GENERATION)

        assert (marked, ok) == (12, True)
        statements = _cypher(neo4j)
        assert len(statements) == 3, "one statement per label"
        for query, params in statements:
            assert f"SET r.{PENDING_SWEEP} = $generation" in query
            assert "IN TRANSACTIONS OF" in query, "a large connector would hit the memory limit part way"
            assert "PERMISSION" in query and "USER_APP_RELATION" in query and "RECORD_LINK" in query
            assert params["generation"] == GENERATION

    @pytest.mark.asyncio
    async def test_mark_inside_a_transaction_runs_unbatched(self, neo4j) -> None:
        neo4j.client.execute_query.return_value = [{"edges": 1}]

        await neo4j.mark_connector_sync_edges("c1", GENERATION, transaction="tx1")

        assert all("IN TRANSACTIONS" not in q for q, _ in _cypher(neo4j))

    @pytest.mark.asyncio
    async def test_clear_untags_this_generation_and_older_ones(self, neo4j) -> None:
        neo4j.client.execute_query.return_value = [{"edges": 2}]

        cleared, ok = await neo4j.clear_connector_sync_edge_tags("c1", GENERATION)

        assert (cleared, ok) == (6, True)
        for query, params in _cypher(neo4j):
            assert f"WHERE r.{PENDING_SWEEP} <= $generation" in query
            assert f"REMOVE r.{PENDING_SWEEP}" in query and "IN TRANSACTIONS OF" in query
            assert params["generation"] == GENERATION

    @pytest.mark.asyncio
    async def test_sweep_deletes_only_edges_still_carrying_this_tag(self, neo4j) -> None:
        neo4j.client.execute_query.return_value = [{"edges": 1}]

        deleted, ok = await neo4j.sweep_connector_sync_edges("c1", GENERATION)

        assert ok
        deletes = [q for q, _ in _cypher(neo4j) if "DELETE r" in q]
        assert len(deletes) == 3
        assert all(f"WHERE r.{PENDING_SWEEP} = $generation" in q for q in deletes)

    @pytest.mark.asyncio
    async def test_sweep_spares_the_collections_it_is_told_to_keep(self, neo4j) -> None:
        neo4j.client.execute_query.return_value = [{"edges": 1}]

        await neo4j.sweep_connector_sync_edges("c1", GENERATION, keep_collections=("userAppRelation",))

        deletes = [q for q, _ in _cypher(neo4j) if "DELETE r" in q]
        assert deletes and all("USER_APP_RELATION" not in q and "PERMISSION" in q for q in deletes)

    @pytest.mark.asyncio
    async def test_sweep_deletes_nothing_when_the_sync_wrote_none_of_the_edges(self, neo4j) -> None:
        neo4j.client.execute_query.return_value = [{"edges": 0}]

        deleted, ok = await neo4j.sweep_connector_sync_edges("c1", GENERATION)

        assert (deleted, ok) == (0, False)
        assert not [q for q, _ in _cypher(neo4j) if "DELETE" in q]
        assert all("USER_APP_RELATION" not in q for q, _ in _cypher(neo4j))

    @pytest.mark.asyncio
    async def test_writes_that_merge_into_an_edge_clear_the_tag(self, neo4j) -> None:
        neo4j.client.execute_query.return_value = [{"matched": 1, "upserted": 1}]
        edge = {"from_id": "r1", "from_collection": "records", "to_id": "r2", "to_collection": "records"}
        await neo4j.create_edges_if_absent([edge], "permission")
        await neo4j.batch_upsert_node_relations([{**edge, "relationshipType": "FOREIGN_KEY"}])
        await neo4j.ensure_app_membership("u1", "users", "c1", is_external=True)
        await neo4j.ensure_team_app_edge("c1", "o1")

        (create_only, create_params), (upsert, _), (membership, membership_params), (team, _) = _cypher(neo4j)
        live = f"r.{PENDING_SWEEP} IS NULL OR NOT r.{PENDING_SWEEP} IN $running"
        assert f"ON MATCH SET r = CASE WHEN {live} THEN properties(r) ELSE edge.props END" in create_only
        assert f"REMOVE r.{PENDING_SWEEP}" in upsert
        assert f"ON MATCH SET r = CASE WHEN {live} THEN properties(r) ELSE $props END" in membership
        assert create_params["running"] == membership_params["running"] == []
        assert f"REMOVE r.{PENDING_SWEEP}" in team


class TestPersonAdoptionKeepsTheSyncsWord:
    """R1-12: a Person's grant the running full sync wrote merges onto the User's
    tagged copy of the same grant; the merged edge must not keep the tag."""

    @staticmethod
    def _arango_script(arango, person_edge: dict) -> list[tuple[str, dict]]:
        from app.models.entities import Person

        arango.get_person_by_email = AsyncMock(return_value=Person(id="p1", email="a@x.io"))
        returns = iter([
            [False],
            [{"mine": [person_edge], "theirs": ["records/r1"], "tagged": ["records/r1"]}],
        ])
        arango.http_client.execute_aql = AsyncMock(side_effect=lambda *a, **k: next(returns, []))
        return _aql(arango)

    @pytest.mark.asyncio
    async def test_arango_a_fresh_person_grant_untags_the_users_copy(self, arango) -> None:
        from app.services.graph_db.common.sync_sweep import full_sync_running

        self._arango_script(arango, {"_key": "e1", "_from": "people/p1", "_to": "records/r1", "role": "READER"})
        with full_sync_running(GENERATION):
            await arango.migrate_person_to_user("a@x.io", "u1", "o1", transaction="t")

        untag = [(q, b) for q, b in _aql(arango) if f"{PENDING_SWEEP}: null" in q]
        assert len(untag) == 1 and untag[0][1]["targets"] == ["records/r1"]
        assert "OPTIONS { keepNull: false }" in untag[0][0], "the key goes, not just its value"
        assert untag[0][1]["user_id"] == "users/u1"

    @pytest.mark.asyncio
    async def test_arango_a_person_grant_the_sync_has_not_written_leaves_the_tag(self, arango) -> None:
        from app.services.graph_db.common.sync_sweep import full_sync_running

        self._arango_script(arango, {"_key": "e1", "_from": "people/p1", "_to": "records/r1",
                                     PENDING_SWEEP: GENERATION})
        with full_sync_running(GENERATION):
            await arango.migrate_person_to_user("a@x.io", "u1", "o1", transaction="t")

        assert not [q for q, _ in _aql(arango) if f"{PENDING_SWEEP}: null" in q]

    @pytest.mark.asyncio
    async def test_neo4j_a_merge_takes_the_tag_off_unless_the_persons_copy_awaits_the_sweep(self, neo4j) -> None:
        from app.services.graph_db.common.sync_sweep import full_sync_running

        neo4j.client.execute_query.return_value = [{"mode": "migrated", "moved_permissions": 1,
                                                    "moved_app_relations": 0}]
        with full_sync_running(GENERATION):
            await neo4j.migrate_person_to_user("a@x.io", "u1", "o1")

        query, params = _cypher(neo4j)[0]
        vouch = (f"ON MATCH SET moved.{PENDING_SWEEP} = CASE WHEN r.{PENDING_SWEEP} IN $running "
                 f"THEN moved.{PENDING_SWEEP} ELSE null END")
        assert query.count(vouch) == 2, "the grant and the gate both merge"
        assert params["running"] == [GENERATION]
