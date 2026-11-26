"""A minimal valid ``RunSpec`` for harness tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import RunSpec

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

_DIGEST = "sha256:" + "0" * 64


def minimal_run_spec(
    *,
    max_turns: int = 8,
    fold_version: int = 1,
    executor: Mapping[str, JsonValue] | None = None,
    max_children: int = 0,
) -> RunSpec:
    """A v2 spec; with ``executor``, the v3 spec that carries it."""
    return validate_contract(
        RunSpec,
        {
            "schemaVersion": 2 if executor is None else 3,
            **({} if executor is None else {"executor": dict(executor)}),
            "harnessMajor": 2,
            "foldVersion": fold_version,
            "runKind": "interactive",
            "principalRef": "p_user",
            "model": {
                "policy": [{"provider": "scripted", "model": "scripted-1"}],
                "promptVersion": "p-test",
            },
            "grants": {"tools": [], "sandboxProfile": "none", "egress": "none"},
            "budgets": {
                "maxTurns": max_turns,
                "maxTokens": 1_000_000,
                "maxCostUsd": 1.0,
                "activeTimeS": 3600,
                "calendarDeadline": "2027-01-01T00:00:00Z",
                "maxChildren": max_children,
            },
            "capabilitiesDigest": _DIGEST,
            "extensions": {},
        },
    )


__all__ = ["minimal_run_spec"]
