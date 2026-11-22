"""States of the licence gate's write side that need no database: the type rule fails closed, the cached licence
answer ages out in both directions, and two connectors sharing one processor keep their own run state.

What users can read in each state is checked on real databases in
tests/integration/graph_db/test_licence_gate_write_real_backends.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import AccessLimit, Connectors, PermissionModel
from app.connectors.core.base.data_processor import permission_sync_gate
from app.connectors.core.base.data_processor.permission_sync_gate import (
    LICENCE_MAX_AGE_SECONDS,
    OPEN_CONNECTOR_TYPES,
    gated,
    permission_sync_licensed,
)
from app.models.entities import AppUser
from tests.unit.connectors.core.test_permission_sync_gate import (
    LICENSED,
    UNLICENSED,
    _app,
    _config,
    _gate_processor,
)


@pytest.mark.parametrize("type_", sorted({c.value for c in Connectors}))
def test_every_registered_type_is_gated_unless_it_is_listed_open(type_: str) -> None:
    """Fail closed: only a type named in OPEN_CONNECTOR_TYPES stays open, so a type added later is gated."""
    assert gated({"type": type_, "scope": "team"}) is (type_.upper() not in OPEN_CONNECTOR_TYPES)
    assert gated({"type": type_, "scope": "personal"}) is False


@pytest.mark.parametrize("type_", ["", None, "brand_new_source", "Jira"])
def test_an_unknown_blank_or_oddly_cased_type_is_gated(type_: str | None) -> None:
    assert gated({"type": type_, "scope": "TEAM"}) is True


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.asyncio
@pytest.mark.parametrize(("before", "after"), [(UNLICENSED, LICENSED), (LICENSED, UNLICENSED), (None, UNLICENSED)])
async def test_a_cached_licence_answer_is_kept_for_its_age_and_never_longer(
    before: object, after: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key entered (or a lapse) is seen within LICENCE_MAX_AGE_SECONDS, not sooner and not later."""
    clock = _Clock()
    monkeypatch.setattr(permission_sync_gate.time, "monotonic", clock)
    config = _config(before)
    first = await permission_sync_licensed(config)
    config.get_config = AsyncMock(return_value=after)

    clock.now += LICENCE_MAX_AGE_SECONDS
    assert await permission_sync_licensed(config) is first, "still the remembered answer"
    clock.now += 1
    assert await permission_sync_licensed(config) is (after == LICENSED or after is None)


@pytest.mark.asyncio
async def test_a_failed_read_after_the_answer_aged_out_raises_and_is_not_taken_for_licensed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    monkeypatch.setattr(permission_sync_gate.time, "monotonic", clock)
    config = _config(UNLICENSED)
    assert await permission_sync_licensed(config) is False
    config.get_config = AsyncMock(side_effect=ConnectionError("down"))
    clock.now += LICENCE_MAX_AGE_SECONDS + 1
    with pytest.raises(ConnectionError):
        await permission_sync_licensed(config)
    config.get_config = AsyncMock(return_value=UNLICENSED)
    assert await permission_sync_licensed(config) is False


@pytest.mark.asyncio
async def test_two_connectors_on_one_processor_keep_their_own_skips_and_lifts() -> None:
    """An org-shared processor: the start of B's run does not forget what A's run skipped, so A is not lifted."""
    proc, _ = _gate_processor(UNLICENSED, _app(limit=AccessLimit.CREATOR.value))

    def user(connector_id: str) -> AppUser:
        return AppUser(app_name=Connectors.JIRA, connector_id=connector_id, source_user_id="s", email="a@x.com",
                       full_name="A")

    await proc.on_new_app_users([user("A")])                   # skipped, noted for A
    proc.config_service = _config(LICENSED)
    assert await proc.apply_licence_gate("B", full_sync=True) is True
    assert await proc.lift_access_limit("A", PermissionModel.RECORD_LEVEL) is False, "A skipped a write this run"
    assert await proc.lift_access_limit("B", PermissionModel.RECORD_LEVEL) is True
    proc.clear_access_limit.assert_awaited_once_with("B", PermissionModel.RECORD_LEVEL)


@pytest.mark.asyncio
@pytest.mark.parametrize("type_", [Connectors.KNOWLEDGE_BASE.value, Connectors.S3.value, Connectors.WEB.value])
async def test_an_open_type_is_never_limited_or_skipped(type_: str) -> None:
    proc, tx_store = _gate_processor(UNLICENSED, _app(type_=type_))
    assert await proc.apply_licence_gate("conn-1", full_sync=True) is False
    proc.set_access_limit.assert_not_awaited()
    assert await proc.skips_permission_writes("conn-1") is False


@pytest.mark.asyncio
async def test_a_missing_app_document_is_not_gated_and_is_not_limited() -> None:
    """A connector whose app is gone (deleted mid-run): nothing to limit, and its writes are not skipped."""
    proc, _ = _gate_processor(UNLICENSED, {})
    assert await proc.apply_licence_gate("conn-1", full_sync=True) is False
    proc.set_access_limit.assert_not_awaited()

