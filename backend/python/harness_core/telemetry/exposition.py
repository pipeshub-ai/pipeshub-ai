"""A process's metrics for the deployment's own scraper (C9.04, C20.48): Prometheus text at ``METRICS_PATH``.

The egress proxy and both gateways listen on no published port, so this is how their registries reach an operator:
each serves ``GET /metrics`` to the caller ``verifier`` accepts (PipesHub: the scrape credential Node provisions, ADR-CRED-01),
and Node's one control-plane route scrapes and merges them. A run's gateway token, all a sandbox ever holds, is never
accepted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.telemetry import security_signals

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from harness_core.sandbox.auth import BrokerAuthVerifier

METRICS_PATH = "/metrics"
PROMETHEUS_TEXT = "text/plain; version=0.0.4; charset=utf-8"


@dataclass(frozen=True)
class MetricsExposition:
    """``text``: the registry's exposition; ``verifier``: who may read it."""

    verifier: BrokerAuthVerifier
    text: Callable[[], str]

    async def answer(self, headers: Mapping[str, str]) -> str | None:
        """The text for a caller the verifier accepts; ``None`` for any other."""
        if await self.verifier.verify(headers, path=METRICS_PATH):
            return self.text()
        security_signals.machine_credential_refused(headers)
        return None


__all__ = ["METRICS_PATH", "PROMETHEUS_TEXT", "MetricsExposition"]
