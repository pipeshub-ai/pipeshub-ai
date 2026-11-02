"""A connector write reaches only its own org's and its own connector's records."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.base.data_processor.data_source_entities_processor import (
    _owned_elsewhere,
)
from tests.unit.connectors.core.test_data_source_entities_processor import (
    _make_processor,
    _make_record,
    _make_tx_store,
)


def _transaction_returning(proc, tx_store) -> None:
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=tx_store)
    ctx.__aexit__ = AsyncMock(return_value=False)
    proc.data_store_provider.transaction = MagicMock(return_value=ctx)


class TestOwnedElsewhere:
    def test_another_org(self) -> None:
        assert _owned_elsewhere({"orgId": "org-2"}, "org-1")

    def test_another_connector(self) -> None:
        assert _owned_elsewhere({"orgId": "org-1", "connectorId": "c2"}, "org-1", "c1")

    def test_own_record(self) -> None:
        assert not _owned_elsewhere({"orgId": "org-1", "connectorId": "c1"}, "org-1", "c1")

    def test_missing_fields_prove_nothing(self) -> None:
        assert not _owned_elsewhere({}, "org-1", "c1")
        assert not _owned_elsewhere({"orgId": "org-2"}, None)

    def test_not_a_document(self) -> None:
        assert not _owned_elsewhere(None, "org-1", "c1")

    def test_a_record_object(self) -> None:
        assert _owned_elsewhere(_make_record(org_id="org-2"), "org-1")
        assert not _owned_elsewhere(_make_record(), "org-1", "conn-1")


class TestACallerIdNamingAnotherRecord:
    """A new record whose id is already another connector's or org's record is not written."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "stored",
        [
            {"_key": "victim", "orgId": "org-1", "connectorId": "other-connector"},
            {"_key": "victim", "orgId": "org-2", "connectorId": "conn-1"},
        ],
    )
    async def test_is_skipped(self, stored: dict) -> None:
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.get_record_by_key = AsyncMock(return_value=stored)
        record = _make_record(id="victim")

        with patch.object(proc, "_handle_new_record", new_callable=AsyncMock) as write:
            result, _ = await proc._process_record(record, [], tx_store)

        assert result is None
        write.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_its_own_record_under_a_new_external_id_is_still_written(self) -> None:
        # A rename that keeps the record id is the reason this lookup exists.
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.get_record_by_key = AsyncMock(
            return_value={"_key": "r1", "orgId": "org-1", "connectorId": "conn-1"}
        )
        record = _make_record(id="r1", external_record_id="ext-renamed")

        with patch.object(proc, "_handle_new_record", new_callable=AsyncMock) as write, \
             patch.object(proc, "_handle_record_permissions", new_callable=AsyncMock):
            await proc._process_record(record, [], tx_store)

        write.assert_awaited_once()


class TestAnExternalIdResolvingToAnotherOrg:
    @pytest.mark.asyncio
    async def test_is_not_upserted(self) -> None:
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.get_record_by_external_id = AsyncMock(return_value=_make_record(org_id="org-2"))

        with patch.object(proc, "_handle_record_permissions", new_callable=AsyncMock) as perms:
            result, _ = await proc._process_record(_make_record(), [], tx_store)

        assert result is None
        perms.assert_not_awaited()
        tx_store.batch_upsert_nodes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_is_not_moved(self) -> None:
        proc = _make_processor()
        proc._get_storage_cleanup = MagicMock(return_value=None)
        tx_store = _make_tx_store()
        tx_store.get_record_by_external_id = AsyncMock(return_value=_make_record(org_id="org-2"))
        _transaction_returning(proc, tx_store)

        with patch.object(proc, "_process_record", new_callable=AsyncMock) as process:
            await proc.on_records_moved([("ext-1", _make_record(external_record_id="ext-2"), [])])

        process.assert_not_awaited()
        # Only the old-record lookup: the move stopped before its duplicate check and writes.
        assert tx_store.get_record_by_external_id.await_count == 1


    @pytest.mark.asyncio
    async def test_a_duplicate_of_another_org_is_not_retired(self) -> None:
        proc = _make_processor()
        proc._get_storage_cleanup = MagicMock(return_value=None)
        tx_store = _make_tx_store()
        old_record = _make_record(id="mine")
        duplicate = _make_record(id="theirs", org_id="org-2", external_record_id="ext-2")

        async def lookup(*_args, visibility=None, **_kwargs) -> object:
            return old_record if visibility is None else duplicate

        tx_store.get_record_by_external_id = AsyncMock(side_effect=lookup)
        _transaction_returning(proc, tx_store)

        await proc.on_records_moved([("ext-1", _make_record(external_record_id="ext-2"), [])])

        tx_store.delete_record_by_key.assert_not_awaited()
        tx_store.batch_upsert_nodes.assert_not_awaited()


class TestPermissionsOfAnotherRecord:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "stored",
        [
            {"_key": "victim", "orgId": "org-1", "connectorId": "other-connector"},
            {"_key": "victim", "orgId": "org-2", "connectorId": "conn-1"},
        ],
    )
    async def test_are_not_replaced(self, stored: dict) -> None:
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.get_record_by_key = AsyncMock(return_value=stored)
        tx_store.get_edges_from_node = AsyncMock(return_value=[{"some": "edge"}])
        _transaction_returning(proc, tx_store)

        await proc.on_updated_record_permissions(_make_record(id="victim"), [])

        tx_store.replace_record_permissions.assert_not_awaited()
        tx_store.batch_update_nodes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_of_its_own_record_are_replaced(self) -> None:
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.get_record_by_key = AsyncMock(
            return_value={"_key": "r1", "orgId": "org-1", "connectorId": "conn-1"}
        )
        tx_store.get_edges_from_node = AsyncMock(return_value=[{"some": "edge"}])
        _transaction_returning(proc, tx_store)

        await proc.on_updated_record_permissions(_make_record(id="r1"), [])

        tx_store.replace_record_permissions.assert_awaited_once()


class TestOrgComesFromTheProcessor:
    @pytest.mark.asyncio
    async def test_a_record_naming_another_org_is_written_to_the_processors_org(self) -> None:
        proc = _make_processor()
        tx_store = _make_tx_store()
        record = _make_record(org_id="org-2")

        with patch.object(proc, "_handle_new_record", new_callable=AsyncMock), \
             patch.object(proc, "_handle_record_permissions", new_callable=AsyncMock):
            await proc._process_record(record, [], tx_store)

        assert record.org_id == "org-1"
        proc.logger.warning.assert_called()


class TestANewRecordGetsNoCallerVectorId:
    @pytest.mark.asyncio
    async def test_a_caller_chosen_virtual_record_id_is_dropped(self) -> None:
        proc = _make_processor()
        tx_store = _make_tx_store()
        record = _make_record(virtual_record_id="someone-elses-vectors")

        with patch.object(proc, "_handle_new_record", new_callable=AsyncMock), \
             patch.object(proc, "_handle_record_permissions", new_callable=AsyncMock):
            await proc._process_record(record, [], tx_store)

        assert record.virtual_record_id is None


class TestDeletesStayInTheOrg:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("soft_delete", [False, True])
    async def test_a_record_of_another_org_is_not_deleted(self, soft_delete: bool) -> None:
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.get_record_by_key = AsyncMock(
            return_value={"_key": "victim", "orgId": "org-2", "connectorId": "c9"}
        )
        _transaction_returning(proc, tx_store)

        with patch(
            "app.connectors.core.base.data_processor.data_source_entities_processor.is_soft_delete_enabled",
            new=AsyncMock(return_value=soft_delete),
        ), patch.object(proc, "on_records_soft_deleted", new_callable=AsyncMock) as soft:
            assert await proc.on_record_deleted("victim") is False

        tx_store.delete_single_record.assert_not_awaited()
        soft.assert_not_awaited()
