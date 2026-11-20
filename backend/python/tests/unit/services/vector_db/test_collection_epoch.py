"""The epoch of a collection: tokens, their states, resets, and the window after which a retired token is ignored."""

import logging
import uuid

import pytest

from app.config.configuration_service import ConfigurationService
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.services.vector_db.collection_epoch import (
    LIVE,
    RETIRED,
    CollectionEpochs,
)

log = logging.getLogger("epoch-test")
WINDOW = 5700.0


def _epochs(clock=None, store=None):  # noqa: ANN202
    config = ConfigurationService(log, store or InMemoryKeyValueStore(log))
    return CollectionEpochs(config, log, **({"clock": clock} if clock else {})), config


@pytest.mark.asyncio
async def test_the_first_capture_creates_the_first_token_and_later_ones_see_it() -> None:
    epochs, _ = _epochs()
    first = await epochs.capture("c")
    assert first.token and (await epochs.capture("c")).token == first.token
    assert (await epochs.states("c"))[first.token][0] == LIVE


@pytest.mark.asyncio
async def test_a_reset_makes_a_new_token_current_before_the_drop_and_retires_the_old_one_after_it() -> None:
    epochs, _ = _epochs()
    before = await epochs.capture("c")
    ticket = await epochs.begin_reset("c")
    assert await epochs.moved(before)  # the drop has not happened, and the writer is already fenced
    assert (await epochs.states("c"))[before.token][0] == LIVE  # not retired until the drop is confirmed
    await epochs.confirm_reset(ticket)
    assert (await epochs.states("c"))[before.token][0] == RETIRED
    assert [d.token for d in await epochs.dead("c")] == [before.token]


@pytest.mark.asyncio
async def test_a_drop_that_fails_leaves_the_old_token_live() -> None:
    epochs, _ = _epochs()
    before = await epochs.capture("c")
    await epochs.begin_reset("c")  # the drop raised: confirm_reset is never reached
    assert (await epochs.states("c"))[before.token][0] == LIVE
    assert await epochs.dead("c") == []


@pytest.mark.asyncio
async def test_a_reset_whose_bump_cannot_be_written_raises_and_changes_nothing() -> None:
    store = InMemoryKeyValueStore(log)
    epochs, config = _epochs(store=store)
    before = await epochs.capture("c")
    real = store.create_key

    async def failing(key, value, overwrite=True, ttl=None):  # noqa: ANN202
        if key.endswith("/current") and overwrite:
            raise ConnectionError("kv down")
        return await real(key, value, overwrite, ttl)

    store.create_key = failing
    with pytest.raises(RuntimeError, match="could not be moved"):
        await epochs.begin_reset("c")
    assert not await epochs.moved(before)


@pytest.mark.asyncio
async def test_the_epoch_writes_only_through_the_configuration_service() -> None:
    """The service is the only way to the store (it also tells other processes' caches); its raw store is never used."""
    from unittest.mock import MagicMock

    config = ConfigurationService(log, InMemoryKeyValueStore(log))

    class _ServiceOnly:
        store = MagicMock()  # anything that reached past the service would fail on this

        def __getattr__(self, name: str):  # noqa: ANN204
            return getattr(config, name)

    epochs = CollectionEpochs(_ServiceOnly(), log)
    before = await epochs.capture("c")
    ticket = await epochs.begin_reset("c")
    await epochs.confirm_reset(ticket)
    assert await epochs.moved(before)
    assert (await epochs.states("c"))[before.token][0] == RETIRED


@pytest.mark.asyncio
async def test_a_token_that_was_created_but_is_not_yet_current_is_never_retired() -> None:
    """Two resets overlap: the second finishes while the first has made its token and not yet pointed at it."""
    epochs, config = _epochs()
    first = await epochs.capture("c")
    ticket_a_holder: list = []
    real = config.store.create_key

    async def pointer_write_runs_a_second_reset(key, value, overwrite=True, ttl=None):  # noqa: ANN202
        if key.endswith("/current") and overwrite and not ticket_a_holder:
            ticket_a_holder.append(None)
            config.store.create_key = real  # the hook runs once
            second = await epochs.begin_reset("c")
            await epochs.confirm_reset(second)
        return await real(key, value, overwrite, ttl)

    config.store.create_key = pointer_write_runs_a_second_reset
    ticket = await epochs.begin_reset("c")
    await epochs.confirm_reset(ticket)
    current = (await epochs.capture("c")).token
    states = await epochs.states("c")
    assert states[current][0] == LIVE  # the token that is current can never have been retired
    assert states[first.token][0] == RETIRED


@pytest.mark.asyncio
async def test_a_late_promotion_cannot_bring_a_retired_token_back() -> None:
    epochs, config = _epochs()
    fence = await epochs.capture("c")
    ticket = await epochs.begin_reset("c")
    await epochs.confirm_reset(ticket)
    # a capture that had read the token before the reset now writes its promotion
    epochs._promoted.discard(fence.token)
    await epochs._promote("c", fence.token)
    assert (await epochs.states("c"))[fence.token][0] == RETIRED


@pytest.mark.asyncio
async def test_a_reset_retires_only_what_it_saw_live() -> None:
    epochs, _ = _epochs()
    await epochs.capture("c")
    ticket = await epochs.begin_reset("c")
    other = await epochs.begin_reset("c")  # a second reset begins before the first one's drop is confirmed
    await epochs.confirm_reset(ticket)
    states = await epochs.states("c")
    retired = {t for t, (state, _) in states.items() if state == RETIRED}
    assert retired == set(ticket.seen)
    assert other.seen.isdisjoint({(await epochs.capture("c")).token})


@pytest.mark.asyncio
async def test_concurrent_resets_leave_both_tokens_and_the_pointer_names_one() -> None:
    epochs, _ = _epochs()
    await epochs.capture("c")
    a, b = await epochs.begin_reset("c"), await epochs.begin_reset("c")
    states = await epochs.states("c")
    assert len(states) == 3 and all(state in (LIVE, RETIRED) for state, _ in states.values())
    assert await epochs.current("c") in states
    assert a and b


@pytest.mark.asyncio
async def test_the_window_runs_from_the_retirement_not_from_the_issue_of_the_token() -> None:
    now = [0.0]
    epochs, _ = _epochs(clock=lambda: now[0])
    await epochs.capture("c")  # issued on day 0
    now[0] += 30 * 86400
    ticket = await epochs.begin_reset("c")
    await epochs.confirm_reset(ticket)  # retired on day 30
    (dead,) = await epochs.dead("c")
    now[0] += 600
    assert not epochs.expired(dead, WINDOW)  # ten minutes after the retirement: a straggler can still write
    now[0] += WINDOW
    assert epochs.expired(dead, WINDOW)


@pytest.mark.asyncio
async def test_a_swept_token_is_not_dead_and_the_index_is_quiet_again() -> None:
    now = [0.0]
    epochs, _ = _epochs(clock=lambda: now[0])
    await epochs.capture("c")
    await epochs.confirm_reset(await epochs.begin_reset("c"))
    assert not await epochs.quiet("c", None)  # inside the window
    (dead,) = await epochs.dead("c")
    await epochs.mark_swept("c", dead.token, None)
    assert await epochs.dead("c") == []
    assert await epochs.quiet("c", None)


@pytest.mark.asyncio
async def test_a_pin_release_makes_the_scope_not_quiet_until_nothing_is_recorded() -> None:
    epochs, _ = _epochs()
    await epochs.capture("c")
    assert await epochs.quiet("c", "o1")
    await epochs.pin_released("c", "o1")
    assert not await epochs.quiet("c", "o1")
    assert await epochs.quiet("c", "o2")  # another org's space is unaffected


@pytest.mark.asyncio
async def test_a_pin_release_that_cannot_be_recorded_raises_so_the_release_does_not_happen() -> None:
    store = InMemoryKeyValueStore(log)
    epochs, _ = _epochs(store=store)
    real = store.create_key

    async def failing(key, value, overwrite=True, ttl=None):  # noqa: ANN202
        if "/pin/" in key:
            raise ConnectionError("kv down")
        return await real(key, value, overwrite, ttl)

    store.create_key = failing
    with pytest.raises(RuntimeError, match="could not be written"):
        await epochs.pin_released("c", "o1")


@pytest.mark.asyncio
async def test_a_capture_whose_promotion_cannot_be_written_raises_and_returns_no_fence() -> None:
    epochs, config = _epochs()
    await epochs.capture("c")
    epochs._promoted.clear()
    real = config.store.create_key

    async def failing(key, value, overwrite=True, ttl=None):  # noqa: ANN202
        if key.endswith("/live"):
            raise ConnectionError("kv down")
        return await real(key, value, overwrite, ttl)

    config.store.create_key = failing
    with pytest.raises(ConnectionError):
        await epochs.capture("c")


@pytest.mark.asyncio
async def test_control_a_bump_through_set_config_swallows_the_failure_the_reset_must_not() -> None:
    """``set_config`` answers False on a store error; the reset writes through the store and raises."""
    store = InMemoryKeyValueStore(log)
    epochs, config = _epochs(store=store)
    await epochs.capture("c")

    async def failing(key, value, overwrite=True, ttl=None):  # noqa: ANN202
        raise ConnectionError("kv down")

    store.create_key = failing
    assert await config.set_config("/x", {"token": "t"}) is False  # swallowed: a cleanup relying on it would go on
    with pytest.raises(ConnectionError):
        await epochs.begin_reset("c")


@pytest.mark.asyncio
async def test_control_measured_from_the_issue_of_the_token_the_window_would_be_over_before_it_began() -> None:
    now = [0.0]
    epochs, config = _epochs(clock=lambda: now[0])
    fence = await epochs.capture("c")
    issued = float((await config.get_config(epochs._state_key("c", fence.token, "live"), use_cache=False))["at"]) / 1000.0
    now[0] += 30 * 86400
    await epochs.confirm_reset(await epochs.begin_reset("c"))
    (dead,) = await epochs.dead("c")
    assert now[0] > issued + WINDOW  # what a window from the issue would say: long over
    assert not epochs.expired(dead, WINDOW)  # the window of the retirement: just begun


@pytest.mark.asyncio
async def test_control_retiring_every_token_that_is_not_current_retires_the_one_being_made() -> None:
    """The N3-1 schedule with the broken rule: confirm retires everything but the pointer's token."""
    epochs, config = _epochs()
    await epochs.capture("c")
    real = config.store.create_key
    holder: list = []

    async def second_reset_confirms_with_the_broken_rule(key, value, overwrite=True, ttl=None):  # noqa: ANN202
        if key.endswith("/current") and overwrite and not holder:
            holder.append(1)
            config.store.create_key = real
            current = await epochs.current("c")
            for token in (await epochs.states("c")):
                if token != current:  # the broken rule: `issuing` tokens included
                    await epochs._create(epochs._state_key("c", token, RETIRED), {"retired_at": 0})
        return await real(key, value, overwrite, ttl)

    config.store.create_key = second_reset_confirms_with_the_broken_rule
    await epochs.begin_reset("c")
    current = await epochs.current("c")
    assert (await epochs.states("c"))[current][0] == RETIRED  # the violation: the token that became current is dead


@pytest.mark.asyncio
async def test_a_token_swept_for_one_space_is_still_dead_in_the_others_and_quiet_only_where_it_was_swept() -> None:
    epochs, _ = _epochs()
    await epochs.capture("c")
    await epochs.confirm_reset(await epochs.begin_reset("c"))
    (dead,) = await epochs.dead("c", "o1")
    await epochs.mark_swept("c", dead.token, "o1")
    assert await epochs.dead("c", "o1") == []
    assert [d.token for d in await epochs.dead("c", "o2")] == [dead.token]
    assert await epochs.quiet("c", "o1") is True
    assert await epochs.quiet("c", "o2") is False
    assert (await epochs.states("c"))[dead.token][0] == RETIRED  # retired for every scope that has not been swept


@pytest.mark.asyncio
async def test_retired_tokens_that_were_only_swept_per_space_are_forgotten_once_many_and_old() -> None:
    now = [0.0]
    epochs, config = _epochs(clock=lambda: now[0])
    await epochs.capture("c")
    first: str | None = None
    for _ in range(20):
        await epochs.confirm_reset(await epochs.begin_reset("c"))
        now[0] += 10  # distinct retirement times
        for dead in await epochs.dead("c", "o1"):
            await epochs.mark_swept("c", dead.token, "o1")
            first = first or dead.token
    now[0] += 2 * WINDOW
    await epochs.confirm_reset(await epochs.begin_reset("c"))
    states = await epochs.states("c")
    assert sum(state == RETIRED for state, _ in states.values()) <= 17
    assert first not in states
    assert first not in await epochs.swept_for("c", "o1")


@pytest.mark.asyncio
async def test_forgetting_an_old_retired_token_removes_the_keys_that_say_where_it_was_swept() -> None:
    epochs, config = _epochs(clock=lambda: 0.0)
    await epochs.capture("c")
    await epochs.confirm_reset(await epochs.begin_reset("c"))
    (first,) = await epochs.dead("c", "o1")
    await epochs.mark_swept("c", first.token, "o1")
    await epochs.mark_swept("c", first.token, "o2")
    keys = await config.list_keys_in_directory(epochs._base("c") + "/swept/")
    assert len(keys) == 2
    now = [10.0]
    epochs._clock = lambda: now[0]
    for _ in range(17):
        now[0] += 10
        await epochs.confirm_reset(await epochs.begin_reset("c"))
    now[0] += 2 * WINDOW
    await epochs.confirm_reset(await epochs.begin_reset("c"))
    assert first.token not in await epochs.states("c")
    assert not await config.list_keys_in_directory(epochs._base("c") + "/swept/")


def test_a_reset_token_has_ids_of_its_own_and_the_first_token_keeps_the_ids_it_always_had() -> None:
    from app.services.vector_db.collection_epoch import RESET_TOKEN_PREFIX, Fence

    base = "7b6a2f2e-0d0a-5b8e-9a43-0c1f7f3a9d11"
    first = Fence("c", "ab" * 16)
    one, two = Fence("c", RESET_TOKEN_PREFIX + "01" * 16), Fence("c", RESET_TOKEN_PREFIX + "02" * 16)
    assert first.point_id(base) == base
    assert len({one.point_id(base), two.point_id(base), base}) == 3
    assert one.point_id(base) == one.point_id(base) and uuid.UUID(one.point_id(base))


@pytest.mark.asyncio
async def test_a_token_made_by_a_reset_is_a_reset_token_and_the_first_one_is_not() -> None:
    from app.services.vector_db.collection_epoch import RESET_TOKEN_PREFIX

    epochs, _ = _epochs()
    first = await epochs.capture("c")
    await epochs.begin_reset("c")
    assert not first.token.startswith(RESET_TOKEN_PREFIX)
    assert (await epochs.capture("c")).token.startswith(RESET_TOKEN_PREFIX)
