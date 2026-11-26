"""The internal-only services' ``GET /metrics`` in PipesHub: the process's registry, to the scraper's credential."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.harness.credential_roles import scrape_only_auth
from app.telemetry.backend import METRICS_BACKEND
from harness_core.telemetry.exposition import MetricsExposition

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService


def worker_metrics_exposition(config_service: ConfigurationService) -> MetricsExposition:
    """Read on every scrape: a rotated credential needs no restart, and the previous one works through its grace."""
    return MetricsExposition(scrape_only_auth(config_service), METRICS_BACKEND.serialize)


__all__ = ["worker_metrics_exposition"]
