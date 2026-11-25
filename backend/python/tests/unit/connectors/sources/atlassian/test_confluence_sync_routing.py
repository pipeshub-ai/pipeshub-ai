"""Confluence incremental sync sends a content change to updateRecord."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.sync_routing import (
    carry_stored_version,
    save_synced_records,
    source_content_changed,
    synced,
)


def _record(**kwargs: object) -> SimpleNamespace:
    base = {"external_revision_id": "1", "version": 1, "is_placeholder": False, "rewrite_permissions": True}
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_a_new_record_is_not_a_content_change() -> None:
    assert source_content_changed(None, _record()) is False


def test_a_placeholder_promotion_is_not_a_content_change() -> None:
    existing = _record(is_placeholder=True, external_revision_id=None, version=0)
    assert source_content_changed(existing, _record(external_revision_id="3", version=1)) is False


def test_an_unchanged_revision_is_not_a_content_change() -> None:
    existing = _record(external_revision_id="4", version=4)
    assert source_content_changed(existing, _record(external_revision_id="4", version=4)) is False


def test_a_moved_revision_is_a_content_change() -> None:
    existing = _record(external_revision_id="1", version=1)
    incoming = _record(external_revision_id="2", version=2)
    assert synced(incoming, [], existing)[2] is True


@pytest.mark.asyncio
async def test_save_routes_a_content_change_and_keeps_the_rest_on_upsert() -> None:
    processor = MagicMock()
    processor.on_new_records = AsyncMock()
    processor.on_record_content_update = AsyncMock()
    fresh = _record(external_revision_id="1", version=0, rewrite_permissions=True)
    changed = _record(external_revision_id="2", version=2, rewrite_permissions=True)
    same = _record(external_revision_id="3", version=3, rewrite_permissions=False)
    grants = [object()]

    await save_synced_records(processor, [
        (fresh, grants, False),
        (changed, grants, True),
        (same, [], False),
    ])

    upserts = processor.on_new_records.await_args.args[0]
    assert [row[0] for row in upserts] == [fresh, same]
    processor.on_record_content_update.assert_awaited_once_with(changed, grants)


@pytest.mark.asyncio
async def test_a_version_zero_row_stays_an_upsert_even_if_flagged() -> None:
    processor = MagicMock()
    processor.on_new_records = AsyncMock()
    processor.on_record_content_update = AsyncMock()
    first = _record(version=0, rewrite_permissions=True)

    await save_synced_records(processor, [(first, [], True)])

    processor.on_new_records.assert_awaited_once()
    processor.on_record_content_update.assert_not_awaited()


def test_a_stored_record_with_a_new_revision_carries_the_next_version() -> None:
    existing = _record(external_revision_id="1", version=4)
    incoming = _record(external_revision_id="2", version=0)
    assert carry_stored_version(incoming, existing) is True
    assert incoming.version == 5


def test_an_unchanged_stored_record_keeps_version_zero_for_the_upsert() -> None:
    existing = _record(external_revision_id="4", version=4)
    incoming = _record(external_revision_id="4", version=0)
    assert carry_stored_version(incoming, existing) is False
    assert incoming.version == 0


def test_a_placeholder_does_not_carry_a_version() -> None:
    existing = _record(is_placeholder=True, external_revision_id=None, version=0)
    incoming = _record(external_revision_id="3", version=0)
    assert carry_stored_version(incoming, existing) is False
    assert incoming.version == 0
