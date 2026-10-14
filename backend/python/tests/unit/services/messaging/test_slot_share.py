"""Unit tests for each connector's share of the index slots
(``scheduling.slot_share``) and the scheduler hooks it uses."""
from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from app.services.messaging.scheduling.drr_scheduler import DRRScheduler
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.messaging.scheduling.slot_share import SlotShare

if TYPE_CHECKING:
    from collections.abc import Callable

LIGHT = "index_light"
HEAVY = "index_heavy"


def _pool_of(key: tuple[str, ...]) -> str:
    return HEAVY if key[-1] == "heavy" else LIGHT


def _limits(light: int = 15, heavy: int = 2) -> Callable[[str], int]:
    return lambda pool: heavy if pool == HEAVY else light


def _config(**overrides) -> FairSchedulerConfig:
    defaults = {
        "enabled": True,
        "key_fields": ("orgId", "connectorId"),
        "default_quantum": 1,
        "max_buffered_messages": 1000,
        "max_per_entity_messages": 500,
        "max_dwell_seconds": 900.0,
        "tier_level": True,
    }
    defaults.update(overrides)
    return FairSchedulerConfig(**defaults)


class _Weights:
    def __init__(self, weights: dict[tuple[str, ...], int]) -> None:
        self._weights = weights

    def quantum_for(self, key: tuple[str, ...]) -> int:
        return self._weights.get(tuple(key), 1)


class TestBookkeeping:
    def test_counts_per_connector_and_pool(self) -> None:
        share = SlotShare(entity_depth=2)
        share.started("m1", ("org", "github", "light"), LIGHT)
        share.started("m2", ("org", "github", "light"), LIGHT)
        share.started("m3", ("org", "github", "heavy"), HEAVY)
        share.started("m4", ("org", "slack", "light"), LIGHT)

        assert share.in_flight(("org", "github"), LIGHT) == 2
        assert share.in_flight(("org", "github"), HEAVY) == 1
        assert share.in_flight(("org", "slack"), LIGHT) == 1
        assert share.in_flight(("org", "slack"), HEAVY) == 0

    def test_finished_is_idempotent_and_ignores_unknown_tokens(self) -> None:
        share = SlotShare(entity_depth=2)
        share.started("m1", ("org", "github", "light"), LIGHT)
        share.finished("m1")
        share.finished("m1")
        share.finished("never-started")
        assert share.in_flight(("org", "github"), LIGHT) == 0

    def test_a_token_is_counted_once(self) -> None:
        share = SlotShare(entity_depth=2)
        share.started("m1", ("org", "github", "light"), LIGHT)
        share.started("m1", ("org", "github", "light"), LIGHT)
        assert share.in_flight(("org", "github"), LIGHT) == 1

    def test_finished_from_many_threads_keeps_counts_exact(self) -> None:
        share = SlotShare(entity_depth=2)
        for i in range(2000):
            share.started(i, ("org", "github", "light"), LIGHT)

        def finish(start: int) -> None:
            for i in range(start, 2000, 4):
                share.finished(i)

        threads = [threading.Thread(target=finish, args=(n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert share.in_flight(("org", "github"), LIGHT) == 0


class TestCaps:
    def test_a_connector_alone_is_not_capped(self) -> None:
        share = SlotShare(entity_depth=2)
        for i in range(40):
            share.started(i, ("org", "github", "light"), LIGHT)
        plan = share.plan([("org", "github", "light")], _pool_of, _limits())
        assert plan.cap_for(("org", "github", "light")) is None
        assert plan.allows(("org", "github", "light"))

    def test_two_waiting_connectors_split_the_pool(self) -> None:
        share = SlotShare(entity_depth=2)
        backlog = [("org", "github", "light"), ("org", "slack", "light")]
        plan = share.plan(backlog, _pool_of, _limits(light=15))
        # ceil(15 / 2): the odd slot is not left idle.
        assert plan.cap_for(("org", "github", "light")) == 8
        assert plan.cap_for(("org", "slack", "light")) == 8

        for i in range(8):
            share.started(i, ("org", "github", "light"), LIGHT)
        plan = share.plan(backlog, _pool_of, _limits(light=15))
        assert not plan.allows(("org", "github", "light"))
        assert plan.allows(("org", "slack", "light"))

    def test_pools_are_shared_out_separately(self) -> None:
        share = SlotShare(entity_depth=2)
        # GitHub waits for both pools, Slack only for light: GitHub is
        # alone in the heavy pool and so uncapped there.
        backlog = [
            ("org", "github", "light"),
            ("org", "github", "heavy"),
            ("org", "slack", "light"),
        ]
        plan = share.plan(backlog, _pool_of, _limits(light=15, heavy=4))
        assert plan.cap_for(("org", "github", "heavy")) is None
        assert plan.cap_for(("org", "github", "light")) == 8

    def test_never_below_one(self) -> None:
        share = SlotShare(entity_depth=2)
        backlog = [("org", f"c{i}", "light") for i in range(40)]
        plan = share.plan(backlog, _pool_of, _limits(light=15))
        assert {plan.cap_for(key) for key in backlog} == {1}

    def test_orgs_split_first_then_their_connectors(self) -> None:
        share = SlotShare(entity_depth=2)
        backlog = [("big", f"c{i}", "light") for i in range(4)] + [
            ("small", "only", "light")
        ]
        plan = share.plan(backlog, _pool_of, _limits(light=16))
        # The one-connector org gets half the pool, not a fifth of it.
        assert plan.cap_for(("small", "only", "light")) == 8
        assert plan.cap_for(("big", "c0", "light")) == 2

    def test_weights_from_the_weight_provider(self) -> None:
        share = SlotShare(
            entity_depth=2, weights=_Weights({("org", "slack"): 3})
        )
        backlog = [("org", "github", "light"), ("org", "slack", "light")]
        plan = share.plan(backlog, _pool_of, _limits(light=16))
        assert plan.cap_for(("org", "slack", "light")) == 12
        assert plan.cap_for(("org", "github", "light")) == 4

    def test_the_cap_follows_the_current_limit(self) -> None:
        share = SlotShare(entity_depth=2)
        backlog = [("org", "github", "light"), ("org", "slack", "light")]
        plan = share.plan(backlog, _pool_of, _limits(light=48))
        assert plan.cap_for(("org", "github", "light")) == 24


class TestSchedulerHooks:
    def test_backlogged_keys_lists_only_non_empty_leaves(self) -> None:
        scheduler: DRRScheduler[str] = DRRScheduler(_config())
        scheduler.enqueue(("org", "github", "light"), "g1")
        scheduler.enqueue(("org", "slack", "light"), "s1")
        scheduler.enqueue(("org", "slack", "heavy"), "s2")
        assert sorted(scheduler.backlogged_keys()) == [
            ("org", "github", "light"),
            ("org", "slack", "heavy"),
            ("org", "slack", "light"),
        ]
        scheduler.dequeue(key_allowed=lambda key: key[1] == "github")
        assert ("org", "github", "light") not in scheduler.backlogged_keys()

    def test_a_disallowed_key_is_skipped_without_losing_its_turn(self) -> None:
        scheduler: DRRScheduler[str] = DRRScheduler(_config())
        for i in range(3):
            scheduler.enqueue(("org", "github", "light"), f"g{i}")
            scheduler.enqueue(("org", "slack", "light"), f"s{i}")

        blocked = {"github"}
        got = [
            scheduler.dequeue(key_allowed=lambda key: key[1] not in blocked)[1]
            for _ in range(2)
        ]
        assert got == ["s0", "s1"]
        blocked.clear()
        # GitHub kept its place at the head of the round.
        assert scheduler.dequeue(key_allowed=lambda key: True)[1] == "g0"

    def test_a_head_not_yet_due_is_not_backlogged(self) -> None:
        """A due record behind it does not count: dequeue only looks at the
        head, so the key cannot take a slot until the head is due."""
        scheduler: DRRScheduler[str] = DRRScheduler(_config())
        scheduler.enqueue(("org", "github", "light"), "g0", not_before=time.time() + 300)
        scheduler.enqueue(("org", "github", "light"), "g1")
        scheduler.enqueue(("org", "slack", "light"), "s0")
        assert scheduler.backlogged_keys() == [("org", "slack", "light")]

    def test_a_head_the_dispatcher_rejects_is_not_backlogged(self) -> None:
        scheduler: DRRScheduler[str] = DRRScheduler(_config())
        scheduler.enqueue(("org", "github", "light"), "g0")
        scheduler.enqueue(("org", "slack", "light"), "s0")
        assert scheduler.backlogged_keys(can_dispatch=lambda item: item != "g0") == [
            ("org", "slack", "light")
        ]

    def test_dequeue_returns_none_when_every_key_is_disallowed(self) -> None:
        scheduler: DRRScheduler[str] = DRRScheduler(_config())
        scheduler.enqueue(("org", "github", "light"), "g0")
        assert scheduler.dequeue(key_allowed=lambda key: False) is None
        assert scheduler.pending_count == 1
