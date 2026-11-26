"""What a run's pinned spec says about its gates (``RunSpec.gates``)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness_core.contracts.generated.run_spec import RunSpec, VerifyGate


def dry_run(spec: RunSpec) -> bool:
    return spec.gates is not None and spec.gates.dry_run is True


def plan_approval(spec: RunSpec) -> bool:
    return spec.gates is not None and spec.gates.plan_approval == "required"


def verify_gate(spec: RunSpec) -> VerifyGate | None:
    return spec.gates.verify if spec.gates is not None else None


__all__ = ["dry_run", "plan_approval", "verify_gate"]
