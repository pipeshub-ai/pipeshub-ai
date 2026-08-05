"""Shared ``ResourceGovernor`` factory for the consumer-concurrency unit
tests (``test_indexing_consumer.py``, ``test_redis_streams_indexing_consumer.py``,
``test_consumer_concurrency_governor.py``), so the governor/probe wiring only
needs to be kept in sync with the ``ResourceGovernor``/``ResourceSnapshot``
constructors in one place.
"""
from __future__ import annotations

import logging

from app.services.resource_governor import ResourceGovernor
from app.services.resource_governor.models import ResourceSnapshot


def make_test_governor(
    *, env_parse: int = 4, env_index: int = 8, logger_name: str = "test.governor",
) -> ResourceGovernor:
    """A governor with deterministic ceilings (explicit env values bypass
    cgroup/CPU derivation entirely, see policy.resolve_ceilings)."""
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
    )
