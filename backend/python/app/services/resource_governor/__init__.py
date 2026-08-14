"""Resource-adaptive admission control shared by the parsing, indexing and
Docling services.

Public surface only — internal modules (``probe``, ``policy``, ``registry``,
``gate``) are implementation details callers should not need to import
directly. See the plan at
``.cursor/plans/adaptive_parsing_concurrency_4684db3c.plan.md`` for the full
design rationale.
"""
from __future__ import annotations

from app.services.resource_governor.admission import acquire_gate_with_backpressure
from app.services.resource_governor.controller import ResourceGovernor
from app.services.resource_governor.gate import AdmissionGate, StartRateLimiter
from app.services.resource_governor.models import (
    Ceilings,
    ControllerState,
    Limits,
    ParseTier,
    Pool,
    PoolDemand,
    PoolState,
    ResourceSnapshot,
)
from app.services.resource_governor.probe import ResourceProbe, build_probe
from app.services.resource_governor.tiers import classify, gate_pool, parse_cost

__all__ = [
    "AdmissionGate",
    "Ceilings",
    "ControllerState",
    "Limits",
    "ParseTier",
    "Pool",
    "PoolDemand",
    "PoolState",
    "ResourceGovernor",
    "ResourceProbe",
    "ResourceSnapshot",
    "StartRateLimiter",
    "acquire_gate_with_backpressure",
    "build_probe",
    "classify",
    "gate_pool",
    "parse_cost",
]
