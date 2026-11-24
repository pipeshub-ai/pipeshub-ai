"""Auto model mode: picks a configured chat model and reasoning effort per turn.

A configured decision model sorts the turn into a need tier. With none saved,
the turn uses the default model. Code maps the tier onto the configured models
by size and thinking (see ``app/services/ai_models/traits.py``).
"""

from app.services.ai_models.auto.service import (
    AUTO_MODEL_KEY,
    AutoDecision,
    AutoRequest,
    is_auto_model_key,
    resolve_auto_model,
    resolve_requested_model,
)

__all__ = [
    "AUTO_MODEL_KEY",
    "AutoDecision",
    "AutoRequest",
    "is_auto_model_key",
    "resolve_auto_model",
    "resolve_requested_model",
]
