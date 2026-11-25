"""The context compiler (REPORT 6.3): P4.1 cache bands, P4.2 the reduction ladder, P4.4 compaction."""

from harness_core.context.compaction import ModelCompaction, TruncationCompaction
from harness_core.context.compiler import PairingBroken, assert_paired, compile_context
from harness_core.context.ladder import (
    LadderPolicy,
    Measurement,
    Reduction,
    plan_reduction,
)
from harness_core.context.ports import (
    CompactionFailed,
    CompactionInputEmpty,
    CompactionRefused,
    CompactionRequest,
    CompactionStrategy,
    CompactionSummary,
)
from harness_core.context.tools import TOOL_ORDER_SLOT, tool_band_order
from harness_core.context.types import (
    Attribution,
    Compaction,
    CompiledContext,
    CompilerInput,
    HistoryItem,
    HostBands,
    PinnedItem,
    SeqSpan,
    ToolEntry,
)

__all__ = [
    "TOOL_ORDER_SLOT",
    "Compaction",
    "CompactionFailed",
    "CompactionInputEmpty",
    "CompactionRefused",
    "CompactionRequest",
    "CompactionStrategy",
    "CompactionSummary",
    "CompiledContext",
    "CompilerInput",
    "HistoryItem",
    "HostBands",
    "LadderPolicy",
    "Measurement",
    "ModelCompaction",
    "PairingBroken",
    "PinnedItem",
    "Attribution",
    "Reduction",
    "SeqSpan",
    "ToolEntry",
    "TruncationCompaction",
    "assert_paired",
    "compile_context",
    "plan_reduction",
    "tool_band_order",
]
