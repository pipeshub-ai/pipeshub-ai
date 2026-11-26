"""Contract suite for ``BrokerMetrics`` adapters (C9.04, C9.07; BU-04 BM-1..BM-3).

Subclass ``BrokerMetricsContract`` with a ``metrics`` fixture (the adapter) and a ``labels`` fixture: a callable
returning every metric label value the adapter has exported so far (cumulative is fine: the suite only asserts what
must never appear).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, get_args

from harness_core.sandbox.metering import MeteredInterval, Phase, WarmClaim
from harness_core.sandbox.types import RuntimeMode

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.sandbox.metering import BrokerMetrics

HOSTILE = "org-hostile-1/run-hostile-2"
BILLING = {"orgId": "org-hostile-1", "runId": "run-hostile-2"}


class BrokerMetricsContract:
    def test_bm1_every_observation_a_broker_makes_is_accepted(self, metrics: BrokerMetrics) -> None:
        for mode in RuntimeMode:
            for phase in get_args(Phase):
                metrics.metered(MeteredInterval("sbx-1", phase, mode.value, 1.5, BILLING))
        for outcome in get_args(WarmClaim):
            metrics.warm_claim(outcome)
        metrics.pool_depth(0)
        metrics.pool_depth(7)

    def test_bm2_no_org_run_sandbox_or_unknown_mode_becomes_a_label(
        self, metrics: BrokerMetrics, labels: Callable[[], set[str]]
    ) -> None:
        metrics.metered(MeteredInterval(HOSTILE, "active", HOSTILE, 2.0, BILLING))
        seen = labels()
        assert not any("hostile" in value for value in seen), seen

    def test_bm3_a_negative_interval_does_not_raise(self, metrics: BrokerMetrics) -> None:
        metrics.metered(MeteredInterval("sbx-1", "active", RuntimeMode.DOCKER_HARDENED.value, -1.0))


__all__ = ["HOSTILE", "BrokerMetricsContract"]
