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


def _outlook_mail() -> dict:
    return {"_key": "m1", "id": "m1", "orgId": "o1", "connectorId": "c1",
            "connectorName": "OUTLOOK", "origin": "CONNECTOR"}


def _stored_mail(provider) -> None:
    stored = MagicMock()
    stored.id, stored.org_id = "m1", "o1"
    provider.get_record_by_external_id = AsyncMock(return_value=stored)


class TestSyncDeleteByExternalId:
    """Outlook's sync delete: with ``soft_delete`` the trash takes the message and its direct attachments."""

    @staticmethod
    def _arango_outlook() -> tuple[ArangoHTTPProvider, list[str]]:
        provider = _arango()
        _stored_mail(provider)
        provider.http_client.get_document = AsyncMock(return_value=_outlook_mail())
        provider.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1"})
        provider._check_record_permission = AsyncMock(return_value="OWNER")
        provider._direct_attachment_ids = AsyncMock(return_value=["a1"])
        removed: list[str] = []
        provider._delete_outlook_edges = AsyncMock()
        provider._delete_file_record = AsyncMock()
        provider._delete_mail_record = AsyncMock()
        provider._delete_main_record = AsyncMock(side_effect=lambda key, txn=None: removed.append(key))
        provider.soft_delete_records = AsyncMock(return_value=soft_delete_result(
            ["m1", "a1"], ["m1", "a1"],
            [{"id": "m1", "vrid": "vm", "orgId": "o1"}, {"id": "a1", "vrid": "va", "orgId": "o1"}], "b1",
        ))
        return provider, removed

    async def test_arango_trashes_the_mail_and_its_direct_attachments_like_the_hard_delete(self) -> None:
        hard, removed = self._arango_outlook()
        await hard.delete_record_by_external_id("c1", "msg-1", "u1")
        hard.soft_delete_records.assert_not_called()

        soft, _ = self._arango_outlook()
        result = await soft.delete_record_by_external_id("c1", "msg-1", "u1", soft_delete=True)

        soft._delete_main_record.assert_not_called()
        args, kwargs = soft.soft_delete_records.await_args
        assert sorted(args[0]) == sorted(removed) == ["a1", "m1"]
        assert (kwargs["delete_source"], kwargs["deleted_by_user_id"], kwargs["follow"]) == ("CONNECTOR", None, ())
        assert result["softDeleted"] is True and result["virtualRecordIds"] == ["vm", "va"]

    async def test_arango_keeps_the_mailbox_owner_check(self) -> None:
        provider, _ = self._arango_outlook()
        provider._check_record_permission = AsyncMock(return_value="READER")
        with pytest.raises(Exception, match="Only mailbox owner"):
            await provider.delete_record_by_external_id("c1", "msg-1", "u1", soft_delete=True)
        provider.soft_delete_records.assert_not_called()

    @staticmethod
    def _neo4j_outlook(mail: dict | None = None) -> Neo4jProvider:
        provider = _neo4j()
        _stored_mail(provider)
        provider.get_document = AsyncMock(side_effect=lambda key, collection, txn=None: (
            (mail or _outlook_mail()) if collection == "records" else None
        ))
        provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        provider.delete_records_and_relations = AsyncMock()
        provider.client.execute_query = AsyncMock(return_value=[{"id": "a1"}])
        provider.soft_delete_records = AsyncMock(return_value=soft_delete_result(
            ["m1", "a1"], ["m1", "a1"],
            [{"id": "m1", "vrid": "vm", "orgId": "o1"}, {"id": "a1", "vrid": "va", "orgId": "o1"}], "b1",
        ))
        return provider

    async def test_neo4j_trashes_the_mail_and_its_direct_attachments_like_arango(self) -> None:
        """Left live, the attachments of a trashed mail would stay searchable."""
        soft = self._neo4j_outlook()
        result = await soft.delete_record_by_external_id("c1", "msg-1", "u1", soft_delete=True)

        soft.delete_records_and_relations.assert_not_called()
        soft.get_user_by_user_id.assert_not_called()
        query = soft.client.execute_query.await_args.args[0]
        assert "relationshipType = 'ATTACHMENT'" in query and "*" not in query
        assert soft.client.execute_query.await_args.kwargs["parameters"] == {"record_id": "m1", "org_id": "o1"}
        args, kwargs = soft.soft_delete_records.await_args
        assert args[0] == ["m1", "a1"]
        assert (kwargs["delete_source"], kwargs["deleted_by_user_id"], kwargs["follow"]) == ("CONNECTOR", None, ())
        assert result["softDeleted"] is True and result["virtualRecordIds"] == ["vm", "va"]

    async def test_neo4j_leaves_the_attachments_of_a_mail_already_in_the_trash_alone(self) -> None:
        """They went with the mail's own batch; a second delete must not trash them under another."""
        provider = self._neo4j_outlook({**_outlook_mail(), "isDeleted": True})
        provider.soft_delete_records = AsyncMock(return_value=soft_delete_result(["m1"], [], [], "b2"))

        result = await provider.delete_record("m1", "u1", "o1", soft_delete=True)

        provider.client.execute_query.assert_not_called()
        assert provider.soft_delete_records.await_args.args[0] == ["m1"]
        assert result["code"] == 404

    @pytest.mark.parametrize("backend", ["arango", "neo4j"])
    async def test_a_message_already_in_the_trash_is_not_looked_up(self, backend) -> None:
        """A redelivered removal must not raise on a record the trash already holds."""
        from app.services.graph_db.common.record_visibility import RecordVisibility

        provider = _arango() if backend == "arango" else _neo4j()
        provider.get_record_by_external_id = AsyncMock(return_value=None)
        assert await provider.delete_record_by_external_id("c1", "msg-1", "u1", soft_delete=True) is None
        assert provider.get_record_by_external_id.await_args.kwargs["visibility"] is RecordVisibility.LIVE


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
