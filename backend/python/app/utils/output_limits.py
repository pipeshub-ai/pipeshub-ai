"""The deployment's per-call model output limit (P4.17), read from ``HARNESS_MODEL_OUTPUT_LIMIT``.

One number, in tokens: the limit of an agent turn and of an engine call through the model gateway; structured
calls and summaries get their smaller limits under it. ``0`` sends no limit, so each provider's own maximum
applies (the behaviour before P4.17). Unset: ``harness_core.models.output_bound.DEFAULT_TURN_LIMIT``.
"""

from __future__ import annotations

from app.utils.env_utils import env_int
from harness_core.models.output_bound import DEFAULT_TURN_LIMIT, OutputLimits

MODEL_OUTPUT_LIMIT_ENV = "HARNESS_MODEL_OUTPUT_LIMIT"
# Below this a reasoning model can spend the whole limit before its first visible token.
MIN_MODEL_OUTPUT_LIMIT = 4_096


def model_output_limits() -> OutputLimits:
    value = env_int(MODEL_OUTPUT_LIMIT_ENV, DEFAULT_TURN_LIMIT, lo=0)
    if not value:
        return OutputLimits.from_turn_limit(None)
    return OutputLimits.from_turn_limit(max(value, MIN_MODEL_OUTPUT_LIMIT))


__all__ = ["MIN_MODEL_OUTPUT_LIMIT", "MODEL_OUTPUT_LIMIT_ENV", "model_output_limits"]
