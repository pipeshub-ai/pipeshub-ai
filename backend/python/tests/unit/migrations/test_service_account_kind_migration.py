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
    # The migration re-reads before recording done; keep answering the same.
    return ServiceAccountKindMigrationService(provider, config, MagicMock()), config


def _staged(*user_ids, disabled=()) -> dict:
    return {
        "version": 1,
        "userIds": list(user_ids),
        "disabledUserIds": list(disabled),
    }


def _provider(keys: dict) -> MagicMock:
    """A graph holding exactly `keys`, answering like the real providers.

    The batched lookup raises for a chunk holding an id it cannot resolve and
    returns nothing for that chunk, so the migration falls back to one lookup
    per id there. Both behaviours are reproduced here.
    """
    provider = MagicMock()

    async def batched(user_ids, *, chunk_size, org_id=None) -> dict[str, str]:
        missing = [u for u in user_ids if u not in keys]
        if missing:
            raise ValueError(f"Users not found in graph: {missing}")
        return {u: keys[u] for u in user_ids}

    async def one(user_id, *, raise_on_error=False) -> dict | None:
        return {"id": keys[user_id]} if user_id in keys else None

    provider.get_graph_user_keys_by_mongo_user_ids = AsyncMock(side_effect=batched)
    provider.get_user_by_user_id = AsyncMock(side_effect=one)
    provider.batch_upsert_nodes = AsyncMock(return_value=True)
    return provider


@pytest.mark.asyncio
async def test_a_staged_account_is_marked_a_service_account() -> None:
    provider = _provider({"u1": "node-1"})
    service, config = _service(provider, _staged("u1"))

    assert (await service.migrate())["stamped"] == 1

    written = provider.batch_upsert_nodes.await_args.args[0]
    assert written == [
        {
            "id": "node-1",
            "kind": "service",
            "updatedAtTimestamp": written[0]["updatedAtTimestamp"],
            "isDisabled": False,
        }
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
        "isDisabled",
    }


@pytest.mark.asyncio
async def test_the_nodes_are_resolved_in_one_batched_lookup() -> None:
    """One round trip per batch, not per account: this runs inside the startup
    health gate."""
    keys = {f"u{i}": f"node-{i}" for i in range(250)}
    provider = _provider(keys)
    service, _ = _service(provider, _staged(*keys))

    assert (await service.migrate())["stamped"] == 250
    # Three chunks, not two hundred and fifty lookups.
    assert provider.get_graph_user_keys_by_mongo_user_ids.await_count == 3
    assert provider.get_user_by_user_id.await_count == 0
    assert provider.batch_upsert_nodes.await_count == 3


@pytest.mark.asyncio
async def test_a_staged_id_with_no_node_is_retried_not_recorded_done() -> None:
    """An unresolved id usually means that account's own userAdded is still in
    flight, so the flag stays unset and a later startup finishes the job."""
    provider = _provider({})
    service, config = _service(provider, _staged("gone"))

    result = await service.migrate()

    assert (result["success"], result["stamped"], result["missing"]) == (True, 0, 1)
    provider.batch_upsert_nodes.assert_not_called()
    # Nothing resolved, so there is no progress to record and nothing to flag.
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_one_straggler_does_not_cost_the_accounts_that_do_have_nodes() -> None:
    """The batched lookup raises for the whole chunk and hands back nothing, so
    without the per-id fallback a single missing id would leave every other
    account unmarked on every boot."""
    provider = _provider({"u1": "node-1"})
    service, config = _service(provider, _staged("u1", "u-not-yet"))

    result = await service.migrate()

    assert (result["success"], result["stamped"], result["missing"]) == (True, 1, 1)
    # Progress is recorded so the next boot skips this node, but never `done`.
    assert config.set_config.await_args.args[1].get("done") is not True
    written = provider.batch_upsert_nodes.await_args.args[0]
    assert written == [
        {
            "id": "node-1",
            "kind": "service",
            "updatedAtTimestamp": written[0]["updatedAtTimestamp"],
            "isDisabled": False,
        }
    ]


@pytest.mark.asyncio
async def test_an_empty_staged_list_finishes_the_backfill() -> None:
    """Staged explicitly as empty means this deployment has no service
    accounts — nothing to mark, and nothing to wait for."""
    provider = _provider({})
    service, config = _service(provider, {"version": 1, "userIds": []})

    assert (await service.migrate())["success"] is True
    assert config.set_config.await_args.args[1]["done"] is True


@pytest.mark.asyncio
async def test_a_store_that_cannot_be_read_is_not_an_empty_key() -> None:
    """Reading that as "nothing staged" makes the boot look idle while the
    backfill silently waits for another restart."""
    provider = _provider({})
    config = MagicMock()
    config.get_config = AsyncMock(side_effect=ConnectionError("etcd down"))
    config.set_config = AsyncMock()
    service = ServiceAccountKindMigrationService(provider, config, MagicMock())

    assert (await service.migrate())["success"] is False
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_a_list_that_changed_while_we_worked_is_not_recorded_done() -> None:
    """The store has no compare-and-set. If user management staged another
    account meanwhile, writing done over it would strand that one."""
    provider = _provider({"u1": "node-1"})
    config = MagicMock()
    config.get_config = AsyncMock(
        side_effect=[_staged("u1"), _staged("u1", "u2")]
    )
    config.set_config = AsyncMock()
    service = ServiceAccountKindMigrationService(provider, config, MagicMock())

    assert (await service.migrate())["stamped"] == 1
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_a_failed_lookup_is_not_read_as_a_missing_node() -> None:
    provider = _provider({})
    provider.get_graph_user_keys_by_mongo_user_ids = AsyncMock(
        side_effect=ConnectionError("graph down")
    )
    service, config = _service(provider, _staged("u1"))
    # Not a ValueError, so it is a failure rather than "this id has no node".

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


@pytest.mark.asyncio
async def test_an_account_already_switched_off_is_marked_disabled() -> None:
    """It emits no event of its own, so the backfill is its only chance."""
    provider = _provider({"u1": "node-1", "u2": "node-2"})
    service, _ = _service(provider, _staged("u1", "u2", disabled=["u2"]))

    await service.migrate()

    written = {d["id"]: d["isDisabled"] for d in provider.batch_upsert_nodes.await_args.args[0]}
    assert written == {"node-1": False, "node-2": True}


@pytest.mark.asyncio
async def test_the_flag_is_written_both_ways_not_only_when_true() -> None:
    """An account enabled again before this ran would otherwise keep a stale
    flag from an earlier pass."""
    provider = _provider({"u1": "node-1"})
    service, _ = _service(provider, _staged("u1"))

    await service.migrate()

    assert provider.batch_upsert_nodes.await_args.args[0][0]["isDisabled"] is False


@pytest.mark.asyncio
async def test_a_second_pass_does_not_rewrite_what_the_first_one_stamped() -> None:
    """While a straggler keeps `done` unset, every later boot would otherwise
    merge the same document over every node that is already correct, moving
    updatedAtTimestamp each time."""
    provider = _provider({"u1": "node-1"})
    staged = _staged("u1", "u-not-yet")
    config = MagicMock()
    config.set_config = AsyncMock()
    # First pass sees the plain staged list; the second sees what it recorded.
    after_first = {**staged, "stampedUserIds": ["u1"]}
    config.get_config = AsyncMock(side_effect=[staged, after_first])
    service = ServiceAccountKindMigrationService(provider, config, MagicMock())

    first = await service.migrate()
    assert (first["stamped"], first["missing"]) == (1, 1)
    assert config.set_config.await_args.args[1]["stampedUserIds"] == ["u1"]

    provider.batch_upsert_nodes.reset_mock()
    second = await service.migrate()

    assert second["stamped"] == 0
    provider.batch_upsert_nodes.assert_not_called()


@pytest.mark.asyncio
async def test_progress_is_not_mistaken_for_completion() -> None:
    """The recorded progress must never carry done, or the straggler is lost."""
    provider = _provider({"u1": "node-1"})
    service, config = _service(provider, _staged("u1", "u-not-yet"))

    await service.migrate()

    assert config.set_config.await_args.args[1].get("done") is not True


@pytest.mark.asyncio
async def test_a_changed_disabled_set_is_not_marked_done() -> None:
    """Same accounts, different answer about which are switched off. This pass
    stamped the old set, and `done` would stop anyone staging the new one."""
    provider = _provider({"u1": "node-1", "u2": "node-2"})
    applied = _staged("u1", "u2")
    newer = _staged("u1", "u2", disabled=["u2"])
    config = MagicMock()
    config.get_config = AsyncMock(side_effect=[applied, newer])
    config.set_config = AsyncMock()
    service = ServiceAccountKindMigrationService(provider, config, MagicMock())

    assert (await service.migrate())["success"] is True
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_progress_does_not_write_a_stale_list_back() -> None:
    """Node replaces the document when the list changes, and that replacement
    carries no progress. Writing the old snapshot over it would undo the new
    list and then mark its ids stamped, so the next start would skip them."""
    provider = _provider({"u1": "node-1"})
    applied = _staged("u1", "u-not-yet")
    newer = _staged("u1", "u-not-yet", "u3")
    config = MagicMock()
    config.get_config = AsyncMock(side_effect=[applied, newer])
    config.set_config = AsyncMock()
    service = ServiceAccountKindMigrationService(provider, config, MagicMock())

    result = await service.migrate()

    assert (result["stamped"], result["missing"]) == (1, 1)
    config.set_config.assert_not_called()
