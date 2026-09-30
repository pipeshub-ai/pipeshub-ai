"""``soft_delete_records`` and the soft branch of ``delete_record``, on both providers.

What the queries mark is checked on real graphs in
tests/integration/test_soft_delete_e2e.py. These pin the failure path and the
routing: a failed mark rolls back and raises, and a UI/API delete reaches the
trash only after the same permission checks, never through the hard delete.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.featureflag.config.config import CONFIG
from app.services.featureflag.platform_settings import is_soft_delete_enabled
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.common.utils import (
    soft_delete_request_result,
    soft_delete_result,
)
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _arango() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(spec=logging.Logger), AsyncMock())
    provider.http_client = AsyncMock()
    return provider


def _neo4j() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    return provider


@pytest.mark.parametrize("backend", ["arango", "neo4j"])
async def test_a_failed_mark_rolls_back_and_raises(backend) -> None:
    provider = _arango() if backend == "arango" else _neo4j()
    provider.begin_transaction = AsyncMock(return_value="txn-1")
    provider.commit_transaction = AsyncMock()
    provider.rollback_transaction = AsyncMock()
    if backend == "arango":
        provider.execute_query = AsyncMock(side_effect=RuntimeError("graph down"))
    else:
        provider.client.execute_query = AsyncMock(side_effect=RuntimeError("graph down"))

    with pytest.raises(RuntimeError, match="graph down"):
        await provider.soft_delete_records(["r1"], "c1", delete_source="USER", batch_id="b1")

    provider.rollback_transaction.assert_awaited_once_with("txn-1")
    provider.commit_transaction.assert_not_called()


@pytest.mark.parametrize("backend", ["arango", "neo4j"])
async def test_nothing_requested_touches_nothing(backend) -> None:
    provider = _arango() if backend == "arango" else _neo4j()
    provider.begin_transaction = AsyncMock()
    result = await provider.soft_delete_records([], "c1", delete_source="USER", batch_id="b1")
    assert result["successfully_deleted"] == 0 and result["batch_id"] == "b1"
    provider.begin_transaction.assert_not_called()


def test_the_result_names_what_was_not_marked() -> None:
    result = soft_delete_result(
        ["r1", "gone"], ["r1"], [{"id": "r1", "vrid": "v1", "orgId": "o1"}, {"id": "c", "vrid": "v1"}], "b1"
    )
    assert result["failed_records"][0]["record_id"] == "gone"
    assert result["virtual_record_ids"] == ["v1"]
    assert (result["org_id"], result["successfully_deleted"]) == ("o1", 1)


def test_an_api_delete_of_an_already_trashed_record_is_not_found() -> None:
    empty = soft_delete_result(["r1"], [], [], "b1")
    assert soft_delete_request_result("r1", {"connectorId": "c1"}, empty)["code"] == 404


class TestApiDeleteRouting:
    async def test_arango_checks_permissions_then_marks(self) -> None:
        provider = _arango()
        record = {"_key": "r1", "orgId": "o1", "connectorId": "kb1", "connectorName": "KB", "origin": "UPLOAD"}
        provider.http_client.get_document = AsyncMock(return_value=record)
        provider.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1"})
        provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        provider._execute_kb_record_deletion = AsyncMock()
        provider.soft_delete_records = AsyncMock(return_value=soft_delete_result(
            ["r1"], ["r1"], [{"id": "r1", "vrid": "v1", "orgId": "o1"}], "b1"
        ))

        result = await provider.delete_record("r1", "u1", "o1", soft_delete=True)

        assert result["softDeleted"] is True and result["virtualRecordIds"] == ["v1"]
        provider._execute_kb_record_deletion.assert_not_called()
        kwargs = provider.soft_delete_records.await_args.kwargs
        assert (kwargs["delete_source"], kwargs["deleted_by_user_id"]) == ("USER", "uk1")

    async def test_arango_refuses_before_marking(self) -> None:
        provider = _arango()
        record = {"_key": "r1", "orgId": "o1", "connectorId": "kb1", "connectorName": "KB", "origin": "UPLOAD"}
        provider.http_client.get_document = AsyncMock(return_value=record)
        provider.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1"})
        provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        provider.get_user_kb_permission = AsyncMock(return_value="READER")
        provider.soft_delete_records = AsyncMock()

        result = await provider.delete_record("r1", "u1", "o1", soft_delete=True)

        assert result["code"] == 403
        provider.soft_delete_records.assert_not_called()

    async def test_neo4j_marks_instead_of_deleting(self) -> None:
        provider = _neo4j()
        record = {"id": "r1", "orgId": "o1", "connectorId": "c1", "connectorName": "DRIVE", "origin": "CONNECTOR"}
        provider.get_document = AsyncMock(return_value=record)
        provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        provider.delete_records_and_relations = AsyncMock()
        provider.soft_delete_records = AsyncMock(return_value=soft_delete_result(
            ["r1"], ["r1"], [{"id": "r1", "vrid": None, "orgId": "o1"}], "b1"
        ))

        result = await provider.delete_record("r1", "u1", "o1", soft_delete=True)

        assert result["softDeleted"] is True
        provider.delete_records_and_relations.assert_not_called()


class TestFlag:
    async def test_off_by_default(self) -> None:
        config = MagicMock()
        config.get_config = AsyncMock(return_value={"featureFlags": {}})
        assert await is_soft_delete_enabled(config) is False

    async def test_read_live_from_platform_settings(self) -> None:
        config = MagicMock()
        config.get_config = AsyncMock(return_value={"featureFlags": {CONFIG.ENABLE_SOFT_DELETE: True}})
        assert await is_soft_delete_enabled(config) is True
        assert config.get_config.await_args.kwargs["use_cache"] is False
