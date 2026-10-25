"""Marking the user nodes that belong to service accounts: what gets written,
what is left alone, and when the backfill is allowed to call itself done."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.migrations.service_account_kind_migration import (
    ServiceAccountKindMigrationService,
)

FLAG = "/migrations/service_account_kind_v1"


def _service(provider, state=None) -> tuple[ServiceAccountKindMigrationService, MagicMock]:
    config = MagicMock()
    config.get_config = AsyncMock(return_value=state)
    config.set_config = AsyncMock()
    return ServiceAccountKindMigrationService(provider, config, MagicMock()), config


def _staged(*user_ids) -> dict:
    return {"version": 1, "userIds": list(user_ids)}


def _provider(nodes: dict) -> MagicMock:
    """A graph whose users are looked up by userId."""
    provider = MagicMock()
    provider.get_user_by_user_id = AsyncMock(side_effect=lambda uid, **_: nodes.get(uid))
    provider.batch_upsert_nodes = AsyncMock(return_value=True)
    return provider


@pytest.mark.asyncio
async def test_a_staged_account_is_marked_a_service_account() -> None:
    provider = _provider({"u1": {"id": "node-1", "email": "svc@x"}})
    service, config = _service(provider, _staged("u1"))

    assert (await service.migrate())["stamped"] == 1

    written = provider.batch_upsert_nodes.await_args.args[0]
    assert written == [
        {"id": "node-1", "kind": "service", "updatedAtTimestamp": written[0]["updatedAtTimestamp"]}
    ]
    assert config.set_config.await_args.args[0] == FLAG
    assert config.set_config.await_args.args[1]["done"] is True


@pytest.mark.asyncio
async def test_nothing_but_kind_is_written() -> None:
    """Replaying the creation event would also force isActive, remake the org
    edge and re-add the account to the All team. A kind update must not."""
    provider = _provider({"u1": {"id": "node-1", "isActive": False, "email": "svc@x"}})
    service, _ = _service(provider, _staged("u1"))

    await service.migrate()

    assert set(provider.batch_upsert_nodes.await_args.args[0][0]) == {
        "id",
        "kind",
        "updatedAtTimestamp",
    }


@pytest.mark.asyncio
async def test_an_account_already_marked_is_left_alone() -> None:
    provider = _provider({"u1": {"id": "node-1", "kind": "service"}})
    service, config = _service(provider, _staged("u1"))

    assert (await service.migrate())["stamped"] == 0
    provider.batch_upsert_nodes.assert_not_called()
    assert config.set_config.await_args.args[1]["done"] is True


@pytest.mark.asyncio
async def test_a_staged_id_with_no_node_creates_nothing() -> None:
    provider = _provider({})
    service, config = _service(provider, _staged("gone"))

    result = await service.migrate()

    assert result["stamped"] == 0
    assert result["missing"] == 1
    provider.batch_upsert_nodes.assert_not_called()
    assert config.set_config.await_args.args[1]["done"] is True


@pytest.mark.asyncio
async def test_a_failed_lookup_is_not_read_as_a_missing_node() -> None:
    provider = MagicMock()
    provider.get_user_by_user_id = AsyncMock(side_effect=ConnectionError("graph down"))
    provider.batch_upsert_nodes = AsyncMock()
    service, config = _service(provider, _staged("u1"))

    assert (await service.migrate())["success"] is False
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_a_failed_write_is_retried_next_startup() -> None:
    provider = _provider({"u1": {"id": "node-1"}})
    provider.batch_upsert_nodes = AsyncMock(side_effect=RuntimeError("write failed"))
    service, config = _service(provider, _staged("u1"))

    assert (await service.migrate())["success"] is False
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_nothing_staged_yet_is_not_done() -> None:
    """The user management service stages on every boot until we answer, so an
    empty key means "not yet", never "nothing to do"."""
    provider = _provider({})
    service, config = _service(provider, None)

    assert (await service.migrate())["skipped"] is True
    config.set_config.assert_not_called()

    service, config = _service(provider, {"version": 1, "userIds": []})
    assert (await service.migrate())["skipped"] is True
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_a_finished_migration_does_not_run_again() -> None:
    provider = _provider({"u1": {"id": "node-1"}})
    service, config = _service(provider, {"version": 1, "done": True})

    assert (await service.migrate())["skipped"] is True
    provider.get_user_by_user_id.assert_not_called()
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_more_accounts_than_one_batch() -> None:
    nodes = {f"u{i}": {"id": f"node-{i}"} for i in range(250)}
    provider = _provider(nodes)
    service, _ = _service(provider, _staged(*nodes))

    assert (await service.migrate())["stamped"] == 250
    assert provider.batch_upsert_nodes.await_count == 3


@pytest.mark.asyncio
async def test_a_node_without_a_key_is_counted_not_written() -> None:
    provider = _provider({"u1": {"email": "svc@x"}})
    service, _ = _service(provider, _staged("u1"))

    result = await service.migrate()

    assert result["missing"] == 1
    provider.batch_upsert_nodes.assert_not_called()
