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


def _provider(keys: dict) -> MagicMock:
    """A graph that resolves the staged userIds to node keys."""
    provider = MagicMock()
    provider.get_graph_user_keys_by_mongo_user_ids = AsyncMock(return_value=keys)
    provider.batch_upsert_nodes = AsyncMock(return_value=True)
    return provider


@pytest.mark.asyncio
async def test_a_staged_account_is_marked_a_service_account() -> None:
    provider = _provider({"u1": "node-1"})
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
    provider = _provider({"u1": "node-1"})
    service, _ = _service(provider, _staged("u1"))

    await service.migrate()

    assert set(provider.batch_upsert_nodes.await_args.args[0][0]) == {
        "id",
        "kind",
        "updatedAtTimestamp",
    }


@pytest.mark.asyncio
async def test_the_nodes_are_resolved_in_one_batched_lookup() -> None:
    """One round trip per batch, not per account: this runs inside the startup
    health gate."""
    keys = {f"u{i}": f"node-{i}" for i in range(250)}
    provider = _provider(keys)
    service, _ = _service(provider, _staged(*keys))

    assert (await service.migrate())["stamped"] == 250
    assert provider.get_graph_user_keys_by_mongo_user_ids.await_count == 1
    assert provider.batch_upsert_nodes.await_count == 3


@pytest.mark.asyncio
async def test_a_staged_id_with_no_node_is_retried_not_recorded_done() -> None:
    """An unresolved id usually means that account's own userAdded is still in
    flight. The lookup raises for it, and the flag stays unset."""
    provider = _provider({})
    provider.get_graph_user_keys_by_mongo_user_ids = AsyncMock(
        side_effect=ValueError("Users not found in graph: ['gone']")
    )
    service, config = _service(provider, _staged("gone"))

    assert (await service.migrate())["success"] is False
    provider.batch_upsert_nodes.assert_not_called()
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_a_failed_lookup_is_not_read_as_a_missing_node() -> None:
    provider = _provider({})
    provider.get_graph_user_keys_by_mongo_user_ids = AsyncMock(
        side_effect=ConnectionError("graph down")
    )
    service, config = _service(provider, _staged("u1"))

    assert (await service.migrate())["success"] is False
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_a_raising_write_is_retried_next_startup() -> None:
    provider = _provider({"u1": "node-1"})
    provider.batch_upsert_nodes = AsyncMock(side_effect=RuntimeError("write failed"))
    service, config = _service(provider, _staged("u1"))

    assert (await service.migrate())["success"] is False
    config.set_config.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [False, None])
async def test_a_write_that_answers_falsy_is_not_counted_as_done(answer) -> None:
    """Neither backend raises for a rejected batch; it answers falsy. Counting
    that as stamped would retire those nodes from the retry."""
    provider = _provider({"u1": "node-1"})
    provider.batch_upsert_nodes = AsyncMock(return_value=answer)
    service, config = _service(provider, _staged("u1"))

    result = await service.migrate()

    assert result["success"] is False
    assert result["stamped"] == 0
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
    provider = _provider({"u1": "node-1"})
    service, config = _service(provider, {"version": 1, "done": True})

    assert (await service.migrate())["skipped"] is True
    provider.get_graph_user_keys_by_mongo_user_ids.assert_not_called()
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_a_second_run_over_marked_nodes_is_harmless() -> None:
    """The flag write is the only thing that stops a repeat; the stamp itself
    has to be safe to apply twice, since a lost flag replays it."""
    provider = _provider({"u1": "node-1"})
    service, _ = _service(provider, _staged("u1"))

    first = await service.migrate()
    second = await service.migrate()

    assert (first["stamped"], second["stamped"]) == (1, 1)
    both = [call.args[0] for call in provider.batch_upsert_nodes.await_args_list]
    assert len(both) == 2
    # The same node, marked the same way; only the timestamp moves.
    assert [doc["id"] for batch in both for doc in batch] == ["node-1", "node-1"]
    assert {doc["kind"] for batch in both for doc in batch} == {"service"}
