"""Shared ``ResourceGovernor`` factory for the consumer-concurrency unit
tests (``test_indexing_consumer.py``, ``test_redis_streams_indexing_consumer.py``,
``test_consumer_concurrency_governor.py``), so the governor/probe wiring only
needs to be kept in sync with the ``ResourceGovernor``/``ResourceSnapshot``
constructors in one place.
"""
from __future__ import annotations

import logging

from app.services.resource_governor import DownstreamFeedback, ResourceGovernor
from app.services.resource_governor.models import ResourceSnapshot


def make_test_governor(
    *,
    env_parse: int | None = None,
    env_index: int | None = 24,
    logger_name: str = "test.governor",
    feedback: DownstreamFeedback | None = None,
) -> ResourceGovernor:
    """A governor with deterministic ceilings: the fixed 4-CPU probe below
    derives heavy=4, and ``env_index`` caps the index total at 24, which
    splits 8 heavy / 16 light and holds light parse to 16 (see
    policy.resolve_ceilings). ``env_parse`` defaults to no cap because
    MAX_CONCURRENT_PARSING caps *both* parse tiers, and the total is wide
    enough that light keeps more than its reserve, so the heavy/light
    distinction most of these tests exist to check survives."""
    snapshot = ResourceSnapshot(
        cpu_quota=4.0,
        cpu_utilisation=0.1,
        cpu_throttled_ratio=0.0,
        cpu_pressure=0.0,
        mem_limit_bytes=8 * 1024 ** 3,
        mem_working_set_bytes=1 * 1024 ** 3,
        source="test",
    )

    class _FixedProbe:
        def snapshot(self) -> ResourceSnapshot:
            return snapshot

    return ResourceGovernor(
        logger=logging.getLogger(logger_name),
        env_parse=env_parse,
        env_index=env_index,
        probe=_FixedProbe(),
        feedback=feedback,
    )
