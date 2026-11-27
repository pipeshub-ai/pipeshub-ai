"""The internal-only services' ``GET /metrics`` in PipesHub: the process's registry, to the scraper's credential."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.harness.credential_roles import scrape_only_auth
from app.harness.plugin_metrics import metrics_text
from harness_core.telemetry.exposition import MetricsExposition

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService


def worker_metrics_exposition(config_service: ConfigurationService) -> MetricsExposition | None:
    """Read on every scrape: a rotated credential needs no restart, and the previous one works through its grace.
    ``None`` (no route) when no plug-in keeps this process's metrics."""
    text = metrics_text()
    return MetricsExposition(scrape_only_auth(config_service), text) if text is not None else None


__all__ = ["worker_metrics_exposition"]
