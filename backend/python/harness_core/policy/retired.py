"""Rollout keys a policy layer stored before CTL-5 may still carry (the retired Labs stages).

A reader strips exactly these and keeps every other dimension: refusing the layer for them would stop its denies
applying (fail open). Shared with Node's ``readPolicyLayer`` through ``contracts/harness/vectors/retired-layer-keys.json``.
"""

from __future__ import annotations

from typing import Any, Final

from pydantic import ValidationError

from harness_core.contracts import dump_contract, validate_contract
from harness_core.contracts.generated.policy.policy_layer import PolicyLayer

RETIRED_ROLLOUT_KEYS: Final = frozenset(
    {
        "ENABLE_COLLABORATOR_TURNS",
        "ENABLE_AGENT_ACTIVITY_PANEL",
        "ENABLE_FOLLOWUP_SUGGESTIONS",
        "ENABLE_DURABLE_ASSISTANT_AGENT",
    }
)


def without_retired_keys(raw: Any) -> Any:  # noqa: ANN401 - a JSON value as stored
    """``raw`` without the retired rollout keys; a rollout section they leave empty goes too."""
    if not isinstance(raw, dict) or not isinstance(raw.get("rollout"), dict):
        return raw
    rollout = {key: value for key, value in raw["rollout"].items() if key not in RETIRED_ROLLOUT_KEYS}
    rest = {key: value for key, value in raw.items() if key != "rollout"}
    return {**rest, "rollout": rollout} if rollout else rest


def read_policy_layer(raw: Any) -> dict[str, Any] | None:  # noqa: ANN401 - a JSON value as stored
    """The layer as its contract accepts it (JSON form), the retired keys stripped; ``None`` when it does not parse."""
    try:
        return dump_contract(validate_contract(PolicyLayer, without_retired_keys(raw)))
    except ValidationError:
        return None


__all__ = ["RETIRED_ROLLOUT_KEYS", "read_policy_layer", "without_retired_keys"]
