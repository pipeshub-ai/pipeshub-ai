"""Scenario definition — the only thing a user edits to change what is measured."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


class ScenarioError(ValueError):
    pass


@dataclass
class Scenario:
    name: str
    #: registry key of the load source, e.g. "minio"
    source: str
    #: source-specific options (endpoints, credentials, sizing)
    source_options: dict[str, Any] = field(default_factory=dict)
    #: number of connectors synced concurrently
    units: int = 1
    #: records seeded per unit
    scale: int = 2000
    #: corpus seed; identical seed => identical corpus
    seed: int = 1337
    #: measured iterations (medianed); the harness always adds `warmup` extra
    repeat: int = 3
    warmup: int = 1
    #: seconds to wait for all units to finish syncing before calling it a timeout
    sync_timeout: int = 1800
    #: seconds between /stats polls per connector
    poll_interval: float = 3.0
    #: API latency probe rate, requests/second across all probes
    probe_rate: float = 2.0
    #: resource sample interval, seconds
    sample_interval: float = 1.0
    #: py-spy sampling; set false to skip profiling entirely
    profile: bool = True
    profile_rate: int = 100
    #: services to flame-graph; must be labels the agent knows
    profile_services: list[str] = field(default_factory=lambda: ["connector", "indexing"])
    #: "soft" deletes connectors and waits for graph cleanup; "none" leaves state alone
    reset: str = "soft"
    #: fire every sync trigger twice, ~200ms apart, to force overlapping syncs
    chaos: bool = False
    chaos_gap_s: float = 0.2

    @classmethod
    def load(cls, path: Path) -> "Scenario":
        raw = _read(path)
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(raw) - known
        if unknown:
            raise ScenarioError(
                f"{path}: unknown key(s): {', '.join(sorted(unknown))}. Known: {', '.join(sorted(known))}"
            )
        scenario = cls(**raw)
        scenario.validate()
        return scenario

    def validate(self) -> None:
        if self.units < 1:
            raise ScenarioError("units must be >= 1")
        if self.scale < 1:
            raise ScenarioError("scale must be >= 1")
        # repeat=0 is legal only alongside profiling: that is the profile-only
        # mode, where the flame graph is the deliverable and there is nothing to
        # average. Without profiling it would run no work at all.
        if self.repeat < 1 and not self.profile:
            raise ScenarioError("repeat must be >= 1 (or >= 0 with profiling on)")
        if self.repeat < 0:
            raise ScenarioError("repeat must be >= 0")
        if self.reset not in ("soft", "none"):
            raise ScenarioError("reset must be 'soft' or 'none'")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise ScenarioError(
                "PyYAML is needed for .yaml scenarios (pip install -r loadtest/requirements.txt), "
                "or write the scenario as .json"
            ) from exc
        return yaml.safe_load(text) or {}
    return json.loads(text)
