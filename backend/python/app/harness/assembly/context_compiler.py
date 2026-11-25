"""The context compiler for a PipesHub durable run (P4.1/P4.2), behind ``HARNESS_CONTEXT_COMPILER``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.utils.env_flags import HARNESS_AUTO_COMPACT, HARNESS_CONTEXT_COMPILER, env_flag
from harness_core.context import ModelCompaction, TruncationCompaction
from harness_core.kernel.budget.pricing import get_context_window
from harness_core.kernel.durable.context_compiler import ContextCompiler
from harness_core.models.transport_model import TransportModel

if TYPE_CHECKING:
    from app.harness.assembly.model import ResolvedModel
    from harness_core.context.ports import CompactionStrategy
    from harness_core.kernel.durable.context_compiler import ContextHost


def context_compiler_enabled() -> bool:
    return env_flag(HARNESS_CONTEXT_COMPILER)


def auto_compact_enabled() -> bool:
    """The ladder's compact rung on shaped requests (LC2FIX-2); ``HARNESS_AUTO_COMPACT=false`` is the operator's off switch."""
    return env_flag(HARNESS_AUTO_COMPACT)


def build_context_compiler(model: ResolvedModel, model_name: str, host: ContextHost) -> ContextCompiler | None:
    """``None`` with the flag off: the kernel assembles requests through the shaper as before.
    On: the window the model's configuration declares (else the pricing table's), and a
    summarising compaction on the run's own transport, which is the metered one when the
    ledger is on (P6.5)."""
    if not context_compiler_enabled():
        return None
    return build_compactor(model, model_name, host)


def build_compactor(model: ResolvedModel, model_name: str, host: ContextHost) -> ContextCompiler:
    """What summarises for the person's ``/compact`` (SLASH2 C2), whatever the flag: the same strategy, on the
    run's own metered transport."""
    window = int(model.context_length) if model.context_length else get_context_window(model_name)
    # GA-15: the compaction role's model when the run's pin maps one; the window stays the run's model's.
    compaction = getattr(model, "compaction", None)
    summariser = compaction if compaction is not None and compaction.transport is not None else model
    strategy: CompactionStrategy = (
        ModelCompaction(TransportModel(summariser.transport, purpose="summary"))
        if summariser.transport is not None
        else TruncationCompaction()
    )
    return ContextCompiler(window_tokens=window, strategy=strategy, host=host)


__all__ = ["auto_compact_enabled", "build_compactor", "build_context_compiler", "context_compiler_enabled"]
