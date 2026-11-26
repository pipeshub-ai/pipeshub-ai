"""Refusals of the model policy and the prompt registry (P4.7)."""

from __future__ import annotations

from harness_core.refusals import HarnessRefusal


class ModelPolicyRefused(HarnessRefusal):
    reason_code = "model_policy_refused"


class ModelRouteNotPinned(ModelPolicyRefused):
    """A route the run did not pin: a fallback never leaves the run's pinned routes."""

    reason_code = "model_route_not_pinned"


class PromptVersionUnknown(ModelPolicyRefused):
    reason_code = "prompt_version_unknown"


class PromptVersionConflict(ModelPolicyRefused):
    """A version is immutable: the same ``(name, version)`` with other text is refused."""

    reason_code = "prompt_version_conflict"


__all__ = ["ModelPolicyRefused", "ModelRouteNotPinned", "PromptVersionConflict", "PromptVersionUnknown"]
