"""Egress proxy control-plane API, version 1: paths and the binding codec.

Errors reuse the broker API's table (``harness_core.sandbox.remote.wire``):
a client raises the same typed ``SandboxError`` the server raised.
"""

from __future__ import annotations

from pydantic import TypeAdapter

from harness_core.sandbox.egress.types import EgressBinding

PREFIX = "/v1"
LIVENESS = "/healthz"
HEALTH = f"{PREFIX}/health"
BINDINGS = f"{PREFIX}/bindings"
# An incident stop: ``{"orgId"?, "sessionId"?}`` revokes the matching bindings that serve a run (``Revocation``).
REVOCATIONS = f"{PREFIX}/revocations"

# Bytes a run received outside its sandboxes (WP-63 part A): ``{"orgId", "runId", "bytes"}`` -> ``{allowed, remaining}``.
VOLUME_CHARGES = f"{PREFIX}/volume/charges"

# One push a person allowed (GIT-FLOW): ``{"sandboxId", "placeholder", "host", "repository", "branch", "credentialRef",
# "ttlS"}`` -> 204; the placeholder is then good for that one push (``push_grants``).
PUSH_GRANTS = f"{PREFIX}/push-grants"

# The upload card: ``{"sandboxId", "host", "runId"}`` -> 204; the sandbox's held read-write website at ``host`` takes
# writes for the run it serves (``site_grants``).
SITE_GRANTS = f"{PREFIX}/site-grants"

# SEC-A-02, the flow rule mid-turn: ``{"sandboxId", "runId", "hold": "held"|"forbidden"}`` -> 204; the sandbox's
# websites are narrowed now as a turn's bind would narrow them (``sites.held_for_flow``). It only ever narrows.
FLOW_HOLDS = f"{PREFIX}/flow-holds"

BINDING = TypeAdapter(EgressBinding)


def binding_path(sandbox_id: str) -> str:
    return f"{BINDINGS}/{sandbox_id}"


__all__ = ["BINDING", "BINDINGS", "FLOW_HOLDS", "HEALTH", "LIVENESS", "PREFIX", "PUSH_GRANTS", "REVOCATIONS", "SITE_GRANTS", "VOLUME_CHARGES", "binding_path"]
