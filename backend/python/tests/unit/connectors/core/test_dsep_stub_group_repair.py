"""A stub record group an earlier build minted is repaired when a record reuses it.

Main's ``_handle_record_group`` wrote the stub with ``orgId ""`` and no edges, so
it hung under nothing (baseline BASE-01, QA STORAGE-KB-02). Reusing it as it is
kept its files out of browse and its selection empty.
"""

from unittest.mock import AsyncMock, MagicMock

from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.models.entities import FileRecord, RecordGroup, RecordGroupType, RecordType


def _processor() -> DataSourceEntitiesProcessor:
    proc = DataSourceEntitiesProcessor(MagicMock(), MagicMock(), AsyncMock())
    proc.org_id = "org-1"
    return proc


def _record() -> FileRecord:
    return FileRecord(
        org_id="org-1", external_record_id="id:f1", record_name="a.txt", origin=OriginTypes.CONNECTOR.value,
        connector_name=Connectors.DROPBOX, connector_id="conn-1", record_type=RecordType.FILE, version=0,
        external_record_group_id="13163053361", record_group_type=RecordGroupType.DRIVE.value,
        is_file=True,
    )


def _stored_group(org_id: str) -> RecordGroup:
    return RecordGroup(
        id="rg-stub", org_id=org_id, name="13163053361", external_group_id="13163053361",
        connector_name=Connectors.DROPBOX, connector_id="conn-1", group_type=RecordGroupType.DRIVE,
    )


def _tx(group: RecordGroup, belongs_to: list[dict]) -> AsyncMock:
    tx = AsyncMock()
    tx.get_record_group_by_external_id = AsyncMock(return_value=group)
    tx.get_edges_from_node = AsyncMock(return_value=belongs_to)
    return tx


def _edges(tx: AsyncMock, collection: str) -> list[tuple[str, str]]:
    return [
        (f"{e['from_collection']}/{e['from_id']}", f"{e['to_collection']}/{e['to_id']}")
        for call in tx.batch_create_edges.await_args_list
        if call.kwargs.get("collection", call.args[1] if len(call.args) > 1 else None) == collection
        for e in call.args[0]
    ]


async def test_a_detached_stub_is_given_its_org_and_hung_under_the_app() -> None:
    proc, group = _processor(), _stored_group(org_id="")
    tx = _tx(group, belongs_to=[])

    assert await proc._handle_record_group(_record(), tx) == "rg-stub"

    [upserted] = tx.batch_upsert_record_groups.await_args.args[0]
    assert upserted.id == "rg-stub" and upserted.org_id == "org-1"
    assert set(_edges(tx, CollectionNames.BELONGS_TO.value)) == {
        ("recordGroups/rg-stub", "apps/conn-1"), ("recordGroups/rg-stub", "organizations/org-1"),
    }
    assert _edges(tx, CollectionNames.NODE_RELATIONS.value) == [("apps/conn-1", "recordGroups/rg-stub")]
    assert _edges(tx, CollectionNames.INHERIT_PERMISSIONS.value) == []


async def test_a_stub_under_a_parent_group_keeps_that_parent_and_gains_only_its_org() -> None:
    proc, group = _processor(), _stored_group(org_id="")
    tx = _tx(group, belongs_to=[{"_from": "recordGroups/rg-stub", "_to": "recordGroups/rg-parent"}])

    await proc._handle_record_group(_record(), tx)

    assert tx.batch_upsert_record_groups.await_args.args[0][0].org_id == "org-1"
    assert _edges(tx, CollectionNames.BELONGS_TO.value) == [("recordGroups/rg-stub", "organizations/org-1")]
    assert _edges(tx, CollectionNames.NODE_RELATIONS.value) == []


async def test_a_group_with_an_org_is_used_without_a_write() -> None:
    proc, group = _processor(), _stored_group(org_id="org-1")
    tx = _tx(group, belongs_to=[])

    assert await proc._handle_record_group(_record(), tx) == "rg-stub"

    tx.batch_upsert_record_groups.assert_not_awaited()
    tx.batch_create_edges.assert_not_awaited()
    tx.get_edges_from_node.assert_not_awaited()
