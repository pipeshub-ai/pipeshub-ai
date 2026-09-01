"""Unit tests for the pure Deficit Round Robin scheduler."""
from __future__ import annotations

import time

import pytest

from app.services.messaging.scheduling.drr_scheduler import DRRScheduler
from app.services.messaging.scheduling.interface import (
    EnqueueResult,
    FairSchedulerConfig,
)


def _config(**overrides) -> FairSchedulerConfig:
    defaults = dict(
        enabled=True,
        key_fields=("orgId",),
        default_quantum=1,
        max_buffered_messages=1000,
        max_per_entity_messages=100,
        max_dwell_seconds=900.0,
    )
    defaults.update(overrides)
    return FairSchedulerConfig(**defaults)


def _drain_keys(scheduler: DRRScheduler[str], n: int) -> list[str]:
    keys = []
    for _ in range(n):
        result = scheduler.dequeue()
        assert result is not None
        keys.append(result[0])
    return keys


class TestEnqueueDequeueBasics:
    def test_single_entity_is_fifo(self):
        scheduler: DRRScheduler[str] = DRRScheduler(_config())
        for i in range(5):
            assert scheduler.enqueue(("org-a",), f"item-{i}") == EnqueueResult.ACCEPTED

        dispatched = [scheduler.dequeue()[1] for _ in range(5)]
        assert dispatched == [f"item-{i}" for i in range(5)]
        assert scheduler.is_empty
        assert scheduler.dequeue() is None

    def test_two_entities_interleave_with_equal_quantum(self):
        scheduler: DRRScheduler[str] = DRRScheduler(_config(default_quantum=1))
        for i in range(3):
            scheduler.enqueue(("org-a",), f"a-{i}")
        for i in range(3):
            scheduler.enqueue(("org-b",), f"b-{i}")

        keys = _drain_keys(scheduler, 6)
        assert keys == [("org-a",), ("org-b",)] * 3

    def test_pending_count_and_pending_count_for(self):
        scheduler: DRRScheduler[str] = DRRScheduler(_config())
        scheduler.enqueue(("org-a",), "1")
        scheduler.enqueue(("org-a",), "2")
        scheduler.enqueue(("org-b",), "3")

        assert scheduler.pending_count == 3
        assert scheduler.pending_count_for(("org-a",)) == 2
        assert scheduler.pending_count_for(("org-b",)) == 1
        assert scheduler.pending_count_for(("org-c",)) == 0
        assert scheduler.active_entity_count == 2

    def test_active_entity_count_drops_when_queue_drains(self):
        scheduler: DRRScheduler[str] = DRRScheduler(_config())
        scheduler.enqueue(("org-a",), "1")
        scheduler.dequeue()
        assert scheduler.active_entity_count == 0
        assert scheduler.pending_count_for(("org-a",)) == 0


class TestFairnessAgainstNoisyEntity:
    def test_noisy_org_does_not_starve_quiet_org(self):
        """10,000 messages for org-a and 5 for org-b: org-b must not sit
        behind org-a's entire backlog."""
        scheduler: DRRScheduler[str] = DRRScheduler(
            _config(max_buffered_messages=20000, max_per_entity_messages=20000)
        )
        for i in range(10_000):
            scheduler.enqueue(("org-a",), f"a-{i}")
        for i in range(5):
            scheduler.enqueue(("org-b",), f"b-{i}")

        dispatch_order = _drain_keys(scheduler, 10)
        # org-b's messages land within the first 10 dispatches, nowhere near
        # position 10,000+ where pure FIFO would put them.
        assert dispatch_order.count(("org-b",)) == 5

    def test_jains_fairness_index_over_synthetic_workload(self):
        """Jain's fairness index over per-key dispatch counts across a mixed
        round: 1.0 is perfectly fair, and DRR with equal quantum on a shared
        round should be close to it even with wildly uneven backlogs."""
        scheduler: DRRScheduler[str] = DRRScheduler(
            _config(max_buffered_messages=20000, max_per_entity_messages=20000)
        )
        backlog_sizes = {("org-a",): 500, ("org-b",): 50, ("org-c",): 5}
        for key, size in backlog_sizes.items():
            for i in range(size):
                scheduler.enqueue(key, f"{key}-{i}")

        # Drain exactly one full round-robin cycle over the still-active keys
        # (i.e. while all three still have items) and measure fairness there.
        rounds_to_check = min(backlog_sizes.values())
        counts = dict.fromkeys(backlog_sizes, 0)
        for _ in range(rounds_to_check * len(backlog_sizes)):
            key, _item = scheduler.dequeue()
            counts[key] += 1

        n = len(counts)
        values = list(counts.values())
        jain_index = (sum(values) ** 2) / (n * sum(v * v for v in values))
        assert jain_index == pytest.approx(1.0, abs=1e-9)


class TestWeightedQuantum:
    def test_higher_quantum_gets_more_consecutive_dispatches(self):
        class Weights:
            def quantum_for(self, key: tuple[str, ...]) -> int:
                return 3 if key == ("org-premium",) else 1

        scheduler = DRRScheduler(_config(default_quantum=1), weights=Weights())
        for i in range(6):
            scheduler.enqueue(("org-premium",), f"p-{i}")
        for i in range(6):
            scheduler.enqueue(("org-basic",), f"b-{i}")

        keys = _drain_keys(scheduler, 8)
        # First turn: org-premium gets 3 in a row (quantum=3), then
        # org-basic gets 1 (quantum=1), then org-premium 3 more, etc.
        assert keys == [
            ("org-premium",), ("org-premium",), ("org-premium",), ("org-basic",),
            ("org-premium",), ("org-premium",), ("org-premium",), ("org-basic",),
        ]

    def test_zero_or_negative_weight_falls_back_to_one(self):
        class Weights:
            def quantum_for(self, key: tuple[str, ...]) -> int:
                return 0

        scheduler: DRRScheduler[str] = DRRScheduler(_config(), weights=Weights())
        scheduler.enqueue(("org-a",), "1")
        scheduler.enqueue(("org-a",), "2")
        # Should not raise or loop forever; falls back to quantum=1 per turn.
        assert scheduler.dequeue()[1] == "1"
        assert scheduler.dequeue()[1] == "2"


class TestNotBeforeEligibility:
    def test_future_not_before_is_skipped_without_consuming_deficit(self):
        scheduler: DRRScheduler[str] = DRRScheduler(_config(default_quantum=5))
        far_future = time.time() + 3600
        scheduler.enqueue(("org-a",), "delayed", not_before=far_future)
        scheduler.enqueue(("org-b",), "ready")

        result = scheduler.dequeue()
        assert result == (("org-b",), "ready")
        # org-a was skipped, not served -- nothing left to dequeue now.
        assert scheduler.dequeue() is None
        assert scheduler.pending_count_for(("org-a",)) == 1

    def test_past_not_before_is_eligible(self):
        scheduler: DRRScheduler[str] = DRRScheduler(_config())
        scheduler.enqueue(("org-a",), "ready-now", not_before=time.time() - 10)
        assert scheduler.dequeue() == (("org-a",), "ready-now")

    def test_only_head_of_queue_is_checked(self):
        """not_before only blocks the item at the head; later same-key items
        are irrelevant until the head clears."""
        scheduler: DRRScheduler[str] = DRRScheduler(_config())
        scheduler.enqueue(("org-a",), "blocked-head", not_before=time.time() + 3600)
        scheduler.enqueue(("org-a",), "behind-it")
        assert scheduler.dequeue() is None
        assert scheduler.pending_count_for(("org-a",)) == 2


class TestCanDispatchEligibility:
    def test_blocked_item_is_skipped_without_consuming_deficit(self):
        scheduler: DRRScheduler[str] = DRRScheduler(_config(default_quantum=5))
        scheduler.enqueue(("org-a",), "partition-busy")
        scheduler.enqueue(("org-b",), "free")

        def can_dispatch(item: str) -> bool:
            return item != "partition-busy"

        result = scheduler.dequeue(can_dispatch=can_dispatch)
        assert result == (("org-b",), "free")
        assert scheduler.dequeue(can_dispatch=can_dispatch) is None
        assert scheduler.pending_count_for(("org-a",)) == 1

    def test_becomes_eligible_once_predicate_allows(self):
        scheduler: DRRScheduler[str] = DRRScheduler(_config())
        scheduler.enqueue(("org-a",), "item")
        blocked = {"item"}

        assert scheduler.dequeue(can_dispatch=lambda i: i not in blocked) is None
        blocked.clear()
        assert scheduler.dequeue(can_dispatch=lambda i: i not in blocked) == (
            ("org-a",),
            "item",
        )


class TestCapacityTriState:
    def test_entity_full_when_per_entity_cap_reached(self):
        scheduler: DRRScheduler[str] = DRRScheduler(
            _config(max_per_entity_messages=2, max_buffered_messages=100)
        )
        assert scheduler.enqueue(("org-a",), "1") == EnqueueResult.ACCEPTED
        assert scheduler.enqueue(("org-a",), "2") == EnqueueResult.ACCEPTED
        assert scheduler.enqueue(("org-a",), "3") == EnqueueResult.ENTITY_FULL
        # Another key is unaffected by org-a's cap.
        assert scheduler.enqueue(("org-b",), "1") == EnqueueResult.ACCEPTED

    def test_buffer_full_when_total_cap_reached_even_for_new_key(self):
        scheduler: DRRScheduler[str] = DRRScheduler(
            _config(max_buffered_messages=2, max_per_entity_messages=100)
        )
        assert scheduler.enqueue(("org-a",), "1") == EnqueueResult.ACCEPTED
        assert scheduler.enqueue(("org-a",), "2") == EnqueueResult.ACCEPTED
        assert scheduler.enqueue(("org-b",), "1") == EnqueueResult.BUFFER_FULL

    def test_buffer_full_checked_before_entity_full(self):
        """When both caps are simultaneously exceedable, BUFFER_FULL takes
        priority since it is the one requiring the caller to stop reading."""
        scheduler: DRRScheduler[str] = DRRScheduler(
            _config(max_buffered_messages=1, max_per_entity_messages=1)
        )
        scheduler.enqueue(("org-a",), "1")
        assert scheduler.enqueue(("org-a",), "2") == EnqueueResult.BUFFER_FULL


class TestPurge:
    def test_purge_removes_matching_items_only(self):
        scheduler: DRRScheduler[str] = DRRScheduler(_config())
        scheduler.enqueue(("org-a",), "keep-1")
        scheduler.enqueue(("org-a",), "drop-1")
        scheduler.enqueue(("org-b",), "drop-2")
        scheduler.enqueue(("org-b",), "keep-2")

        removed = scheduler.purge(lambda item: item.startswith("drop"))
        assert sorted(removed) == ["drop-1", "drop-2"]
        assert scheduler.pending_count == 2
        assert scheduler.pending_count_for(("org-a",)) == 1
        assert scheduler.pending_count_for(("org-b",)) == 1

    def test_purge_drops_key_entirely_when_all_items_match(self):
        scheduler: DRRScheduler[str] = DRRScheduler(_config())
        scheduler.enqueue(("org-a",), "drop-1")
        scheduler.enqueue(("org-a",), "drop-2")
        scheduler.enqueue(("org-b",), "keep")

        scheduler.purge(lambda item: item.startswith("drop"))
        assert scheduler.active_entity_count == 1
        assert scheduler.dequeue() == (("org-b",), "keep")

    def test_purge_of_empty_scheduler_is_noop(self):
        scheduler: DRRScheduler[str] = DRRScheduler(_config())
        assert scheduler.purge(lambda item: True) == []


class TestDrainAll:
    def test_drain_all_returns_every_item_and_empties_scheduler(self):
        scheduler: DRRScheduler[str] = DRRScheduler(_config())
        scheduler.enqueue(("org-a",), "1")
        scheduler.enqueue(("org-a",), "2")
        scheduler.enqueue(("org-b",), "3")

        drained = scheduler.drain_all()
        assert sorted(drained) == [(("org-a",), "1"), (("org-a",), "2"), (("org-b",), "3")]
        assert scheduler.is_empty
        assert scheduler.active_entity_count == 0
        assert scheduler.dequeue() is None

    def test_drain_all_on_empty_scheduler(self):
        scheduler: DRRScheduler[str] = DRRScheduler(_config())
        assert scheduler.drain_all() == []


class TestHierarchicalFairness:
    """Two levels: fair between orgs, and between connectors within an org.

    This is the property a flat composite key cannot give. Keying on
    ``"org:connector"`` as one string would let an org with fifty connectors
    take fifty shares; keying on ``orgId`` alone gives a single-org install
    one queue and no fairness at all.
    """

    def _two_level(self, **overrides) -> DRRScheduler[str]:
        defaults = {
            "key_fields": ("orgId", "connectorId"),
            "max_buffered_messages": 100_000,
            "max_per_entity_messages": 100_000,
        }
        defaults.update(overrides)
        return DRRScheduler(_config(**defaults))

    def test_orgs_split_evenly_despite_uneven_connector_counts(self):
        """The headline multi-tenant property. Org A runs five connectors,
        Org B one; they must still split capacity in half."""
        scheduler = self._two_level()
        for connector in range(5):
            for i in range(100):
                scheduler.enqueue(("org-a", f"conn-{connector}"), f"a-{connector}-{i}")
        for i in range(100):
            scheduler.enqueue(("org-b", "conn-0"), f"b-{i}")

        first_200 = [scheduler.dequeue()[0][0] for _ in range(200)]

        assert first_200.count("org-a") == 100
        assert first_200.count("org-b") == 100

    def test_connectors_split_evenly_within_one_org(self):
        """The single-org OSS case: two users syncing at once, one with a
        huge backlog. Neither may starve the other."""
        scheduler = self._two_level()
        for i in range(500):
            scheduler.enqueue(("org-a", "user-1"), f"big-{i}")
        for i in range(10):
            scheduler.enqueue(("org-a", "user-2"), f"small-{i}")

        order = [scheduler.dequeue()[0][1] for _ in range(20)]

        assert order == ["user-1", "user-2"] * 10

    def test_small_user_finishes_early_behind_a_large_backlog(self):
        scheduler = self._two_level()
        for i in range(1000):
            scheduler.enqueue(("org-a", "user-1"), f"big-{i}")
        for i in range(5):
            scheduler.enqueue(("org-a", "user-2"), f"small-{i}")

        completions = []
        while not scheduler.is_empty:
            completions.append(scheduler.dequeue()[0][1])

        last_small = max(
            i for i, user in enumerate(completions) if user == "user-2"
        )
        assert last_small < 10, (
            "the small user's last record should land in the first rounds, "
            f"not behind the 1000-record backlog (got {last_small})"
        )

    def test_org_weight_does_not_reweight_connectors_inside_it(self):
        """A weight provider gives one org a bigger share; its connectors
        still split that share evenly between themselves."""

        class Weights:
            def quantum_for(self, key: tuple[str, ...]) -> int:
                # Only the org level is weighted (a one-element prefix).
                if len(key) == 1 and key[0] == "org-premium":
                    return 4
                return 1

        scheduler = DRRScheduler(
            _config(
                key_fields=("orgId", "connectorId"),
                max_buffered_messages=100_000,
                max_per_entity_messages=100_000,
            ),
            weights=Weights(),
        )
        for connector in ("c1", "c2"):
            for i in range(20):
                scheduler.enqueue(("org-premium", connector), f"p-{connector}-{i}")
        for i in range(20):
            scheduler.enqueue(("org-basic", "c1"), f"b-{i}")

        first_20 = [scheduler.dequeue()[0] for _ in range(20)]
        orgs = [key[0] for key in first_20]
        assert orgs.count("org-premium") == 16
        assert orgs.count("org-basic") == 4

        premium_connectors = [key[1] for key in first_20 if key[0] == "org-premium"]
        assert premium_connectors.count("c1") == 8
        assert premium_connectors.count("c2") == 8

    def test_per_entity_cap_applies_to_the_leaf_not_the_org(self):
        """Each connector gets its own allowance; two connectors in one org
        can hold twice the cap between them."""
        scheduler = self._two_level(max_per_entity_messages=2)

        assert scheduler.enqueue(("org-a", "c1"), "1") == EnqueueResult.ACCEPTED
        assert scheduler.enqueue(("org-a", "c1"), "2") == EnqueueResult.ACCEPTED
        assert scheduler.enqueue(("org-a", "c1"), "3") == EnqueueResult.ENTITY_FULL
        # A different connector in the same org is unaffected.
        assert scheduler.enqueue(("org-a", "c2"), "4") == EnqueueResult.ACCEPTED
        assert scheduler.pending_count_for(("org-a",)) == 3

    def test_rejected_enqueue_creates_no_empty_branches(self):
        scheduler = self._two_level(max_per_entity_messages=1)
        scheduler.enqueue(("org-a", "c1"), "1")
        scheduler.enqueue(("org-a", "c1"), "2")  # ENTITY_FULL
        scheduler.enqueue(("org-new", "c9"), "x")
        scheduler.dequeue()
        scheduler.dequeue()

        assert scheduler.is_empty
        assert scheduler.active_entity_count == 0
        assert scheduler.active_count_at(0) == 0

    def test_pending_count_for_accepts_a_prefix(self):
        scheduler = self._two_level()
        scheduler.enqueue(("org-a", "c1"), "1")
        scheduler.enqueue(("org-a", "c2"), "2")
        scheduler.enqueue(("org-b", "c1"), "3")

        assert scheduler.pending_count_for(("org-a",)) == 2
        assert scheduler.pending_count_for(("org-a", "c1")) == 1
        assert scheduler.pending_count_for(("org-a", "missing")) == 0
        assert scheduler.pending_count_for(("nobody",)) == 0

    def test_active_counts_per_level(self):
        scheduler = self._two_level()
        scheduler.enqueue(("org-a", "c1"), "1")
        scheduler.enqueue(("org-a", "c2"), "2")
        scheduler.enqueue(("org-b", "c1"), "3")

        assert scheduler.active_count_at(0) == 2   # orgs
        assert scheduler.active_entity_count == 3  # connector instances

    def test_blocked_connector_does_not_stall_its_org(self):
        """Eligibility skipping has to work at every level: one connector
        blocked on its Kafka partition must not hold up the rest of the org."""
        scheduler = self._two_level()
        scheduler.enqueue(("org-a", "blocked"), "blocked-item")
        scheduler.enqueue(("org-a", "free"), "free-item")

        result = scheduler.dequeue(can_dispatch=lambda i: i != "blocked-item")

        assert result == (("org-a", "free"), "free-item")

    def test_org_with_only_blocked_work_does_not_stall_other_orgs(self):
        scheduler = self._two_level()
        scheduler.enqueue(("org-a", "c1"), "blocked-item")
        scheduler.enqueue(("org-b", "c1"), "free-item")

        result = scheduler.dequeue(can_dispatch=lambda i: i != "blocked-item")

        assert result == (("org-b", "c1"), "free-item")
        # Skipped without spending deficit: org-a keeps its turn.
        assert scheduler.pending_count_for(("org-a",)) == 1

    def test_purge_prunes_empty_branches_at_every_level(self):
        scheduler = self._two_level()
        scheduler.enqueue(("org-a", "c1"), "drop-1")
        scheduler.enqueue(("org-a", "c2"), "keep")
        scheduler.enqueue(("org-b", "c1"), "drop-2")

        removed = scheduler.purge(lambda item: item.startswith("drop"))

        assert sorted(removed) == ["drop-1", "drop-2"]
        assert scheduler.pending_count == 1
        assert scheduler.active_count_at(0) == 1
        assert scheduler.dequeue() == (("org-a", "c2"), "keep")

    def test_drain_all_returns_fully_qualified_keys(self):
        scheduler = self._two_level()
        scheduler.enqueue(("org-a", "c1"), "1")
        scheduler.enqueue(("org-b", "c2"), "2")

        assert sorted(scheduler.drain_all()) == [
            (("org-a", "c1"), "1"),
            (("org-b", "c2"), "2"),
        ]
        assert scheduler.is_empty
        assert scheduler.active_count_at(0) == 0


class TestKeyDepthNormalization:
    """A key that disagrees with ``key_fields`` must not corrupt the tree
    shape -- a custom extractor or a config change mid-flight would
    otherwise leave nodes addressable at the wrong depth."""

    def test_short_key_is_padded(self):
        scheduler = DRRScheduler(_config(key_fields=("orgId", "connectorId")))
        scheduler.enqueue(("org-a",), "item")

        assert scheduler.dequeue() == (("org-a", "__default__"), "item")

    def test_long_key_is_trimmed(self):
        scheduler = DRRScheduler(_config(key_fields=("orgId",)))
        scheduler.enqueue(("org-a", "conn-1", "extra"), "item")

        assert scheduler.dequeue() == (("org-a",), "item")

    def test_padded_and_exact_keys_land_in_the_same_queue(self):
        scheduler = DRRScheduler(_config(key_fields=("orgId", "connectorId")))
        scheduler.enqueue(("org-a",), "first")
        scheduler.enqueue(("org-a", "__default__"), "second")

        assert scheduler.pending_count_for(("org-a", "__default__")) == 2
