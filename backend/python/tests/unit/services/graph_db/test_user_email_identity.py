"""Unit tests for verified-email graph identity helpers and merge orchestration."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j import neo4j_provider as neo4j_module
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.graph_db.user_email_identity import (
    STUB_EDGE_COLLECTIONS,
    GraphUserEmailConflictError,
    classify_email_peer,
    graph_user_key,
)


class TestClassifyEmailPeer:
    def test_same_graph_key_is_self(self):
        assert classify_email_peer("u1", "k1", {"id": "k1", "userId": "other"}) == "self"

    def test_same_user_id_is_self(self):
        assert classify_email_peer("u1", "k1", {"id": "k2", "userId": "u1"}) == "self"

    def test_mongo_object_id_other_user_is_login(self):
        peer = {"id": "stub-key", "userId": "507f1f77bcf86cd799439011"}
        assert classify_email_peer("aaaaaaaaaaaaaaaaaaaaaaaa", "keep", peer) == "login"

    def test_active_object_id_peer_is_login(self):
        peer = {"id": "n", "userId": "507f1f77bcf86cd799439011", "isActive": True}
        assert classify_email_peer("aaaaaaaaaaaaaaaaaaaaaaaa", "keep", peer) == "login"

    def test_object_id_peer_with_unknown_active_flag_is_still_login(self):
        peer = {"id": "n", "userId": "507f1f77bcf86cd799439011", "isActive": None}
        assert classify_email_peer("aaaaaaaaaaaaaaaaaaaaaaaa", "keep", peer) == "login"

    def test_inactive_stub_with_24_hex_connector_account_id_is_stub(self):
        # older Atlassian account ids look exactly like a Mongo ObjectId
        peer = {"id": "stub-uuid", "userId": "5b10ac8d82e05b22cc7d4ef5", "isActive": False}
        assert classify_email_peer("aaaaaaaaaaaaaaaaaaaaaaaa", "keep", peer) == "stub"

    def test_connector_source_id_is_stub(self):
        assert classify_email_peer("u1", "k1", {"id": "s1", "userId": "google-123"}) == "stub"

    def test_missing_user_id_is_stub(self):
        assert classify_email_peer("u1", "k1", {"id": "s1"}) == "stub"

    def test_graph_user_key_prefers_id(self):
        assert graph_user_key({"id": "a", "_key": "b"}) == "a"
        assert graph_user_key({"_key": "b"}) == "b"


async def _run_apply(provider, *, keep, peers, absorb_error=None, apps=None):
    provider.get_user_apps = apps if apps is not None else AsyncMock(return_value=[])
    provider.get_user_by_user_id = AsyncMock(return_value=keep)
    provider._list_graph_users_by_email = AsyncMock(return_value=peers)
    provider._absorb_graph_user_stub = AsyncMock(side_effect=absorb_error)
    provider.begin_transaction = AsyncMock(return_value="txn")
    provider.commit_transaction = AsyncMock()
    provider.rollback_transaction = AsyncMock()
    provider.batch_upsert_nodes = AsyncMock(return_value=True)
    return await provider.apply_verified_user_email("aaaaaaaaaaaaaaaaaaaaaaaa", "org-1", "b@x.com")


class TestNeo4jApplyVerifiedUserEmail:
    def test_merges_stub_then_sets_email(self):
        async def _run() -> None:
            provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
            keep = {"id": "keep-key", "userId": "aaaaaaaaaaaaaaaaaaaaaaaa"}
            stub = {"id": "stub-key", "userId": "google-99"}
            result = await _run_apply(provider, keep=keep, peers=[keep, stub])
            assert result["email"] == "b@x.com"
            assert result["mergedStubKeys"] == ["stub-key"]
            provider._absorb_graph_user_stub.assert_awaited_once_with("keep-key", "stub-key", "txn")
            payload = provider.batch_upsert_nodes.await_args.args[0][0]
            assert payload["email"] == "b@x.com"
            assert payload["id"] == "keep-key"
            provider.commit_transaction.assert_awaited_once()

        asyncio.run(_run())

    def test_conflict_does_not_write(self):
        async def _run() -> None:
            provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
            keep = {"id": "keep-key", "userId": "aaaaaaaaaaaaaaaaaaaaaaaa"}
            other = {"id": "other-key", "userId": "507f1f77bcf86cd799439011"}
            with pytest.raises(GraphUserEmailConflictError):
                await _run_apply(provider, keep=keep, peers=[other])
            provider.begin_transaction.assert_not_called()

        asyncio.run(_run())

    def test_missing_login_user_returns_none(self):
        async def _run() -> None:
            provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
            provider.get_user_by_user_id = AsyncMock(return_value=None)
            assert await provider.apply_verified_user_email("u1", "o1", "a@b.com") is None

        asyncio.run(_run())


class TestArangoApplyVerifiedUserEmail:
    def test_merges_stub_then_sets_email(self):
        async def _run() -> None:
            provider = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
            keep = {"_key": "keep-key", "userId": "aaaaaaaaaaaaaaaaaaaaaaaa"}
            stub = {"id": "stub-key", "userId": "google-99"}
            result = await _run_apply(provider, keep=keep, peers=[stub])
            assert result["mergedStubKeys"] == ["stub-key"]
            provider._absorb_graph_user_stub.assert_awaited_once()
            assert provider.batch_upsert_nodes.await_args.args[1] == CollectionNames.USERS.value

        asyncio.run(_run())

    def test_conflict_does_not_write(self):
        async def _run() -> None:
            provider = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
            keep = {"_key": "keep-key", "userId": "aaaaaaaaaaaaaaaaaaaaaaaa"}
            other = {"id": "other-key", "userId": "507f1f77bcf86cd799439011"}
            with pytest.raises(GraphUserEmailConflictError):
                await _run_apply(provider, keep=keep, peers=[other])
            provider.begin_transaction.assert_not_called()

        asyncio.run(_run())

    def test_failed_write_rolls_back(self):
        async def _run() -> None:
            provider = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
            keep = {"_key": "keep-key", "userId": "aaaaaaaaaaaaaaaaaaaaaaaa"}
            stub = {"id": "stub-key", "userId": "google-99"}
            with pytest.raises(RuntimeError):
                await _run_apply(
                    provider, keep=keep, peers=[stub], absorb_error=RuntimeError("boom")
                )
            provider.rollback_transaction.assert_awaited_once()
            provider.commit_transaction.assert_not_called()

        asyncio.run(_run())


class TestNeo4jFailureAndSelfOnly:
    def test_failed_write_rolls_back(self):
        async def _run() -> None:
            provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
            keep = {"id": "keep-key", "userId": "aaaaaaaaaaaaaaaaaaaaaaaa"}
            stub = {"id": "stub-key", "userId": "google-99"}
            with pytest.raises(RuntimeError):
                await _run_apply(
                    provider, keep=keep, peers=[keep, stub], absorb_error=RuntimeError("boom")
                )
            provider.rollback_transaction.assert_awaited_once()
            provider.commit_transaction.assert_not_called()

        asyncio.run(_run())

    def test_only_the_login_has_the_email_so_nothing_is_absorbed(self):
        async def _run() -> None:
            provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
            keep = {"id": "keep-key", "userId": "aaaaaaaaaaaaaaaaaaaaaaaa"}
            result = await _run_apply(provider, keep=keep, peers=[keep])
            assert result["mergedStubKeys"] == []
            provider._absorb_graph_user_stub.assert_not_called()
            assert provider.batch_upsert_nodes.await_args.args[0][0]["email"] == "b@x.com"

        asyncio.run(_run())

    def test_several_stubs_with_the_email_are_all_absorbed(self):
        async def _run() -> None:
            provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
            keep = {"id": "keep-key", "userId": "aaaaaaaaaaaaaaaaaaaaaaaa"}
            stubs = [{"id": "s1", "userId": "jira-1"}, {"id": "s2", "userId": "google-2"}]
            result = await _run_apply(provider, keep=keep, peers=[keep, *stubs])
            assert result["mergedStubKeys"] == ["s1", "s2"]
            assert provider._absorb_graph_user_stub.await_count == 2

        asyncio.run(_run())


class TestEmailLookupStaysInsideTheOrg:
    def test_neo4j_lookup_filters_on_org_and_email(self):
        async def _run() -> None:
            provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
            provider.client = MagicMock()
            provider.client.execute_query = AsyncMock(return_value=[])

            await provider._list_graph_users_by_email("new@email.com", "org-1")

            query = provider.client.execute_query.await_args.args[0]
            params = provider.client.execute_query.await_args.kwargs["parameters"]
            assert "u.orgId = $org_id" in query
            assert "toLower(u.email) = toLower($email)" in query
            assert params == {"email": "new@email.com", "org_id": "org-1"}

        asyncio.run(_run())

    def test_arango_lookup_filters_on_org_and_email(self):
        async def _run() -> None:
            provider = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
            provider.http_client = MagicMock()
            provider.http_client.execute_aql = AsyncMock(return_value=[])

            await provider._list_graph_users_by_email("new@email.com", "org-1")

            query = provider.http_client.execute_aql.await_args.args[0]
            bind_vars = provider.http_client.execute_aql.await_args.kwargs["bind_vars"]
            assert "user.orgId == @org_id" in query
            assert "LOWER(user.email) == LOWER(@email)" in query
            assert bind_vars == {"email": "new@email.com", "org_id": "org-1"}

        asyncio.run(_run())


class TestMergedConnectorsAreReportedForCacheInvalidation:
    @pytest.mark.parametrize(
        ("provider_cls", "keep"),
        [
            (Neo4jProvider, {"id": "keep-key", "userId": "aaaaaaaaaaaaaaaaaaaaaaaa"}),
            (ArangoHTTPProvider, {"_key": "keep-key", "userId": "aaaaaaaaaaaaaaaaaaaaaaaa"}),
        ],
    )
    def test_reports_connectors_of_every_merged_stub_read_before_the_stub_is_deleted(
        self, provider_cls, keep
    ):
        async def _run() -> None:
            provider = provider_cls(logger=MagicMock(), config_service=MagicMock())
            order: list[str] = []

            async def apps(key):
                order.append(f"apps:{key}")
                return {
                    "stub-1": [{"id": "conn-b"}, {"_key": "conn-a"}],
                    "stub-2": [{"id": "conn-a"}, {}],
                }[key]

            result = await _run_apply(
                provider,
                keep=keep,
                peers=[
                    {"id": "stub-1", "userId": "jira-1"},
                    {"id": "stub-2", "userId": "google-2"},
                ],
                apps=apps,
                absorb_error=lambda *_: order.append("absorb"),
            )

            assert result["connectorIds"] == ["conn-a", "conn-b"]
            assert order == ["apps:stub-1", "apps:stub-2", "absorb", "absorb"]

        asyncio.run(_run())

    def test_connector_lookup_failure_does_not_block_the_merge(self):
        async def _run() -> None:
            provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
            result = await _run_apply(
                provider,
                keep={"id": "keep-key", "userId": "aaaaaaaaaaaaaaaaaaaaaaaa"},
                peers=[{"id": "stub-1", "userId": "jira-1"}],
                apps=AsyncMock(side_effect=RuntimeError("graph down")),
            )

            assert result["mergedStubKeys"] == ["stub-1"]
            assert result["connectorIds"] == []
            provider.commit_transaction.assert_awaited_once()

        asyncio.run(_run())

    def test_no_stubs_means_no_connectors(self):
        async def _run() -> None:
            provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
            keep = {"id": "keep-key", "userId": "aaaaaaaaaaaaaaaaaaaaaaaa"}
            result = await _run_apply(provider, keep=keep, peers=[keep])
            assert result["connectorIds"] == []

        asyncio.run(_run())


class TestStubEdgesMoveOntoTheLogin:
    def test_neo4j_moves_allowed_edges_skips_others_and_deletes_the_stub(self):
        async def _run() -> None:
            permission_rel = neo4j_module.EDGE_COLLECTION_TO_RELATIONSHIP[
                CollectionNames.PERMISSION.value
            ]
            record_label = neo4j_module.COLLECTION_TO_LABEL[CollectionNames.RECORDS.value]
            provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
            provider.client = MagicMock()
            provider.client.execute_query = AsyncMock(
                side_effect=[
                    [
                        {
                            "rel_type": permission_rel,
                            "from_id": "stub-key",
                            "to_id": "rec-1",
                            "from_labels": ["User"],
                            "to_labels": [record_label],
                            "props": {"role": "READER"},
                        },
                        {
                            "rel_type": permission_rel,
                            "from_id": "stub-key",
                            "to_id": "rec-1",
                            "from_labels": ["User"],
                            "to_labels": [record_label],
                            "props": {"role": "READER"},
                        },
                        {
                            "rel_type": "SOME_CONNECTOR_ONLY_REL",
                            "from_id": "stub-key",
                            "to_id": "rec-2",
                            "from_labels": ["User"],
                            "to_labels": [record_label],
                            "props": {},
                        },
                    ],
                    None,
                ]
            )
            provider.delete_nodes = AsyncMock(return_value=True)

            await provider._absorb_graph_user_stub("keep-key", "stub-key", "txn")

            assert provider.client.execute_query.await_count == 2
            merge_call = provider.client.execute_query.await_args_list[1]
            assert merge_call.kwargs["parameters"] == {
                "from_id": "keep-key",
                "to_id": "rec-1",
                "props": {"role": "READER"},
            }
            assert merge_call.kwargs["txn_id"] == "txn"
            provider.delete_nodes.assert_awaited_once_with(
                ["stub-key"], CollectionNames.USERS.value, transaction="txn"
            )

        asyncio.run(_run())

    def test_neo4j_moves_authenticated_as_per_connector_and_drive_edges(self):
        async def _run() -> None:
            auth_rel = neo4j_module.EDGE_COLLECTION_TO_RELATIONSHIP[
                CollectionNames.AUTHENTICATED_AS.value
            ]
            drive_rel = neo4j_module.EDGE_COLLECTION_TO_RELATIONSHIP[
                CollectionNames.USER_DRIVE_RELATION.value
            ]
            drive_label = neo4j_module.COLLECTION_TO_LABEL[CollectionNames.DRIVES.value]

            def auth(creator, to, connector_id):
                return {
                    "rel_type": auth_rel,
                    "from_id": creator,
                    "to_id": to,
                    "from_labels": ["User"],
                    "to_labels": ["User"],
                    "props": {"connectorId": connector_id},
                }

            provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
            provider.client = MagicMock()
            provider.client.execute_query = AsyncMock(
                side_effect=[
                    [
                        auth("admin", "stub-key", "conn-1"),
                        auth("admin", "stub-key", "conn-2"),
                        auth("admin", "stub-key", "conn-2"),
                        auth("keep-key", "stub-key", "conn-3"),
                        auth("stub-key", "someone", "conn-4"),
                        {
                            "rel_type": drive_rel,
                            "from_id": "stub-key",
                            "to_id": "drive-1",
                            "from_labels": ["User"],
                            "to_labels": [drive_label],
                            "props": {"access_level": "reader"},
                        },
                    ],
                    *[None] * 4,
                ]
            )
            provider.delete_nodes = AsyncMock(return_value=True)

            await provider._absorb_graph_user_stub("keep-key", "stub-key", "txn")

            merges = [c for c in provider.client.execute_query.await_args_list[1:]]
            assert len(merges) == 4
            params = [m.kwargs["parameters"] for m in merges]
            assert [(p["from_id"], p["to_id"], p.get("identity_connectorId")) for p in params] == [
                ("admin", "keep-key", "conn-1"),
                ("admin", "keep-key", "conn-2"),
                ("keep-key", "someone", "conn-4"),
                ("keep-key", "drive-1", None),
            ]
            assert "connectorId: $identity_connectorId" in merges[0].args[0]
            assert "identity_" not in merges[3].args[0]

        asyncio.run(_run())

    def test_neo4j_keeps_every_entity_relation_type_between_a_record_and_the_stub(self):
        async def _run() -> None:
            entity_rel = neo4j_module.EDGE_COLLECTION_TO_RELATIONSHIP[
                CollectionNames.ENTITY_RELATIONS.value
            ]
            record_label = neo4j_module.COLLECTION_TO_LABEL[CollectionNames.RECORDS.value]

            def relation(edge_type, record="rec-1", stub_is_start=False):
                return {
                    "rel_type": entity_rel,
                    "from_id": "stub-key" if stub_is_start else record,
                    "to_id": record if stub_is_start else "stub-key",
                    "from_labels": ["User"] if stub_is_start else [record_label],
                    "to_labels": [record_label] if stub_is_start else ["User"],
                    "props": {"edgeType": edge_type},
                }

            provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
            provider.client = MagicMock()
            provider.client.execute_query = AsyncMock(
                side_effect=[
                    [
                        relation("CREATED_BY"),
                        relation("ASSIGNED_TO"),
                        relation("ASSIGNED_TO"),
                        relation("REPORTED_BY", stub_is_start=True),
                    ],
                    *[None] * 3,
                ]
            )
            provider.delete_nodes = AsyncMock(return_value=True)

            await provider._absorb_graph_user_stub("keep-key", "stub-key", "txn")

            merges = provider.client.execute_query.await_args_list[1:]
            assert len(merges) == 3
            params = [m.kwargs["parameters"] for m in merges]
            assert [
                (p["from_id"], p["to_id"], p["identity_edgeType"]) for p in params
            ] == [
                ("rec-1", "keep-key", "CREATED_BY"),
                ("rec-1", "keep-key", "ASSIGNED_TO"),
                ("keep-key", "rec-1", "REPORTED_BY"),
            ]
            assert "edgeType: $identity_edgeType" in merges[0].args[0]
            assert all(p["props"]["edgeType"] == p["identity_edgeType"] for p in params)

        asyncio.run(_run())

    def test_arango_entity_relations_dedupe_per_edge_type(self):
        async def _run() -> None:
            provider = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
            provider.http_client = MagicMock()
            provider.http_client.execute_aql = AsyncMock(return_value=[])
            provider.delete_nodes = AsyncMock(return_value=True)

            await provider._absorb_graph_user_stub("keep-key", "stub-key", "txn")

            collection = CollectionNames.ENTITY_RELATIONS.value
            queries = [c.args[0] for c in provider.http_client.execute_aql.await_args_list]
            move = next(q for q in queries if "INSERT" in q and f"INTO {collection}" in q)
            assert "other.edgeType == e.edgeType" in move

        asyncio.run(_run())

    def test_arango_authenticated_as_dedupes_per_connector_only(self):
        async def _run() -> None:
            provider = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
            provider.http_client = MagicMock()
            provider.http_client.execute_aql = AsyncMock(return_value=[])
            provider.delete_nodes = AsyncMock(return_value=True)

            await provider._absorb_graph_user_stub("keep-key", "stub-key", "txn")

            queries = [c.args[0] for c in provider.http_client.execute_aql.await_args_list]
            moves = {
                collection: next(q for q in queries if "INSERT" in q and f"INTO {collection}" in q)
                for collection in STUB_EDGE_COLLECTIONS
            }
            assert "other.connectorId == e.connectorId" in moves[CollectionNames.AUTHENTICATED_AS.value]
            for collection, query in moves.items():
                if collection != CollectionNames.AUTHENTICATED_AS.value:
                    assert "connectorId" not in query

        asyncio.run(_run())

    def test_arango_rewrites_every_stub_edge_collection_then_deletes_the_stub(self):
        async def _run() -> None:
            provider = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
            provider.http_client = MagicMock()
            provider.http_client.execute_aql = AsyncMock(return_value=[])
            provider.delete_nodes = AsyncMock(return_value=True)

            await provider._absorb_graph_user_stub("keep-key", "stub-key", "txn")

            queries = [c.args[0] for c in provider.http_client.execute_aql.await_args_list]
            assert len(queries) == 2 * len(STUB_EDGE_COLLECTIONS)
            for collection in STUB_EDGE_COLLECTIONS:
                assert sum(f"IN {collection}" in q for q in queries) == 2
            assert CollectionNames.USER_DRIVE_RELATION.value in STUB_EDGE_COLLECTIONS
            assert CollectionNames.AUTHENTICATED_AS.value in STUB_EDGE_COLLECTIONS
            assert CollectionNames.ENTITY_RELATIONS.value in STUB_EDGE_COLLECTIONS
            first = provider.http_client.execute_aql.await_args_list[0]
            assert first.kwargs["bind_vars"] == {
                "stub_id": "users/stub-key",
                "keep_id": "users/keep-key",
            }
            provider.delete_nodes.assert_awaited_once_with(
                ["stub-key"], CollectionNames.USERS.value, transaction="txn"
            )

        asyncio.run(_run())
