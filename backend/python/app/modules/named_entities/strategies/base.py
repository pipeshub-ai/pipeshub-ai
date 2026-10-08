"""What an extraction strategy returns. Mentions are always kept."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.domain.models import TerminationReason
from app.modules.named_entities.mentions import RawMention
from app.modules.named_entities.normalizers.dates import NormalizationContext
from app.modules.named_entities.text import TextUnit


@dataclass
class ExtractionDocument:
    units: list[TextUnit]
    record_name: str = ""
    record_type: str = ""
    token_estimate: int = 0


@dataclass
class ExtractionContext:
    org_id: str
    norm: NormalizationContext
    enabled: frozenset[EntityKind]
    budgets: object
    llm: object | None = None
    transport: object | None = None
    model_name: str = "indexing"
    provider_name: str = "indexing"
    supports_tools: bool | None = None
    under_pressure: bool = False


@dataclass
class StrategyResult:
    mentions: list[RawMention] = field(default_factory=list)
    termination_reason: TerminationReason = "deterministic"
    turns: int = 0
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    submitted: int = 0
    rejected: int = 0
    rejected_reasons: dict[str, int] = field(default_factory=dict)
    failed_before_first_turn: bool = False
    tools_unsupported: bool = False
    # Part of the document was never sent to the model, or a call over it failed.
    incomplete: bool = False
    windows_total: int = 0
    windows_run: int = 0
    windows_failed: int = 0


class IExtractionStrategy(Protocol):
    name: str

    async def extract(
        self, doc: ExtractionDocument, seed: list[RawMention], ctx: ExtractionContext
    ) -> StrategyResult: ...
