"""Unit tests for verified-email graph identity helpers and merge orchestration."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.graph_db.user_email_identity import (
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

    def test_connector_source_id_is_stub(self):
        assert classify_email_peer("u1", "k1", {"id": "s1", "userId": "google-123"}) == "stub"

    def test_missing_user_id_is_stub(self):
        assert classify_email_peer("u1", "k1", {"id": "s1"}) == "stub"

    def test_graph_user_key_prefers_id(self):
        assert graph_user_key({"id": "a", "_key": "b"}) == "a"
        assert graph_user_key({"_key": "b"}) == "b"


async def _run_apply(provider, *, keep, peers):
    provider.get_user_by_user_id = AsyncMock(return_value=keep)
    provider._list_graph_users_by_email = AsyncMock(return_value=peers)
    provider._absorb_graph_user_stub = AsyncMock()
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
