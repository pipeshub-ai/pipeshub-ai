"""What every broker checks before it creates a sandbox with an egress allowlist (P5.4 §1.1, §1.5).

One place, so the fake and every real adapter refuse the same specs with the same reasons.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.sandbox.egress.policy import policy_errors
from harness_core.sandbox.egress.types import EgressBinding
from harness_core.sandbox.errors import InvalidSandboxSpec, PolicyUnenforceable

if TYPE_CHECKING:
    from harness_core.sandbox.egress.addresses import EgressSubnet
    from harness_core.sandbox.types import SandboxSpec

EGRESS_PROXY_MISSING = "egress_proxy_missing"
EGRESS_POLICY_INVALID = "egress_policy_invalid"


def admit_egress(spec: SandboxSpec, *, proxy_configured: bool) -> bool:
    """Whether the sandbox needs a proxy binding; refuses a spec no binding could honour."""
    if not spec.egress.needs_proxy:
        return False
    if not proxy_configured:
        raise PolicyUnenforceable("egress allowlists need the egress proxy (P5.4)", reason=EGRESS_PROXY_MISSING)
    errors = policy_errors(spec.egress)
    if errors:
        raise InvalidSandboxSpec("; ".join(errors), reason=EGRESS_POLICY_INVALID)
    return True


def binding_of(sandbox_id: str, subnet: EgressSubnet, spec: SandboxSpec) -> EgressBinding:
    return EgressBinding(
        sandbox_id=sandbox_id,
        proxy_address=subnet.proxy_address,
        sandbox_address=subnet.sandbox_address,
        policy=spec.egress,
        labels=spec.labels,
    )


__all__ = ["EGRESS_POLICY_INVALID", "EGRESS_PROXY_MISSING", "admit_egress", "binding_of"]
