"""The strategy `resolve_strategy()` (see `factory.py`) returns for any
`(provider, model)` whose `CacheCapability.mode == "none"` — genuinely
inert, adding zero extra kwargs, for the strict OpenAI-compatible
servers (Ollama, LM Studio, vLLM, ...) that 400 on an unrecognized
field (see the plan's corner case on this).

Re-exported from `agent_loop_lib.cache.base` rather than redefined:
that module's `NoopStrategy` already does exactly this, and duplicating
it here would be the "reinvented four things that already exist"
mistake the plan calls out (review note 3), just for a fifth thing.
"""

from app.agent_loop_lib.cache.base import NoopStrategy

__all__ = ["NoopStrategy"]
