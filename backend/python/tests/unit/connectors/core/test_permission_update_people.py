"""A sharing change reaches the processor as a permission update only, so a record whose
connector read who it is shared with has its person edges rewritten there too."""

from unittest.mock import AsyncMock, patch

from app.models.entities import SourcePerson
from tests.unit.connectors.core.test_permission_change_cache_invalidation import _NOTIFY, _processor, _record

_LINK = "app.connectors.core.base.data_processor.data_source_entities_processor.link_record_people"


def _tx() -> AsyncMock:
    tx = AsyncMock()
    tx.get_edges_from_node = AsyncMock(return_value=[{"to_id": "rg-key"}])
    tx.get_record_group_by_external_id = AsyncMock(return_value=None)
    return tx


async def test_a_permission_update_with_known_shared_with_relinks_people() -> None:
    tx = _tx()
    proc = _processor(tx, [])
    record = _record()
    record.shared_with = [SourcePerson(email="bob@example.com")]

    with patch(_NOTIFY, new=AsyncMock()), patch(_LINK, new=AsyncMock(return_value=1)) as link:
        await proc.on_updated_record_permissions(record, [])

    link.assert_awaited_once()
    assert link.await_args.args[0] is record
    assert link.await_args.args[1] is tx


async def test_an_unshare_relinks_people_with_nobody_shared() -> None:
    proc = _processor(_tx(), [])
    record = _record()
    record.shared_with = []

    with patch(_NOTIFY, new=AsyncMock()), patch(_LINK, new=AsyncMock(return_value=0)) as link:
        await proc.on_updated_record_permissions(record, [])

    link.assert_awaited_once()


async def test_a_permission_update_without_shared_with_leaves_people_alone() -> None:
    proc = _processor(_tx(), [])
    record = _record()

    with patch(_NOTIFY, new=AsyncMock()), patch(_LINK, new=AsyncMock()) as link:
        await proc.on_updated_record_permissions(record, [])

    link.assert_not_awaited()
