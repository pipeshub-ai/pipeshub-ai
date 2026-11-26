"""Read-only tools for one record. Nothing here writes to the graph."""

from __future__ import annotations

import asyncio
import json
import secrets
from typing import TYPE_CHECKING, TypeVar

from pydantic import ValidationError

from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.builtin.planning.task_complete import (
    TaskCompletionOutcome,
)
from app.agent_loop_lib.tools.tags import TAG_LIFECYCLE_TERMINAL
from app.modules.named_entities.domain.kinds import EntityKind, EntitySource, spec_for
from app.modules.named_entities.grounding import Grounder
from app.modules.named_entities.guardrails import name_rejection
from app.modules.named_entities.mentions import RawMention
from app.modules.named_entities.normalizers import normalize_value
from app.modules.named_entities.recognizers.pattern import is_suppressed_secret
from app.modules.named_entities.strategies.agent.staging import StagingSet
from app.modules.named_entities.wire import (
    WireEntity,
    parse_wire_kind,
    wire_item_schema,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.agent_loop_lib.core.types import ToolCall
    from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
    from app.agent_loop_lib.tools.base import Tag
    from app.modules.named_entities.normalizers.dates import NormalizationContext
    from app.modules.named_entities.text import TextUnit

T = TypeVar("T")

_WINDOW = 5
# A window of _WINDOW blocks stays under 16k characters. A longer block is shown
# 3,000 characters at a time, each part pointing at the read that continues it.
_MAX_UNIT_CHARS = 3_000
# Short blocks (chat lines, list items) fill a window up to this many characters,
# the single-call window size, instead of costing the model a turn per five lines.
_WINDOW_CHARS = 4_000
_MAX_HITS = 20
_MAX_SUBMIT_ITEMS = 100
_MAX_RETRIES = 2


class ExtractionSession:
    def __init__(
        self,
        units: list[TextUnit],
        *,
        enabled: frozenset[EntityKind],
        norm_ctx: NormalizationContext,
        seed_count: int,
    ) -> None:
        self.units = units
        self.enabled = enabled
        self.norm_ctx = norm_ctx
        self.staging = StagingSet()
        self.grounder = Grounder(units)
        # Blocks shown to their end, and how far each block has been shown
        # without a gap, so a long block read only in part still counts as unread.
        self.read: set[int] = set()
        self.shown: dict[int, int] = {}
        self.boundary = secrets.token_hex(4)
        self.seed_count = seed_count
        self.submits = 0
        # Submits that filled the per-call cap: the model may have more to send.
        self.full_submits = 0
        self.retries: dict[tuple, int] = {}
        self.finished = False
        self._staging_lock = asyncio.Lock()

    async def staged(self, stage: Callable[[], T]) -> T:
        """Run staging off the event loop: fuzzy grounding is CPU work that would
        stall every record on the loop and its lease heartbeats. The lock keeps
        tool calls of one turn, which run concurrently, from staging at once."""
        async with self._staging_lock:
            return await asyncio.to_thread(stage)

    def window(self, start: int, count: int = _WINDOW, offset: int = 0) -> list[TextUnit]:
        """Blocks from index ``start``: at least ``count`` of them (at most
        ``_WINDOW``), then more while what ``show`` renders stays within ``_WINDOW_CHARS``."""
        count = max(1, min(count, _WINDOW))
        chosen: list[TextUnit] = []
        used = 0
        for unit in self.units:
            if unit.block_index < start:
                continue
            skip = offset if not chosen and unit.block_index == start else 0
            # Rendered size, tag and newline included: one-character blocks are mostly tag.
            size = len(_render_line(unit, skip)[0]) + 1
            if len(chosen) >= count and used + size > _WINDOW_CHARS:
                break
            chosen.append(unit)
            used += size
        return chosen

    def window_count(self) -> int:
        """Windows a front-to-back read takes, a long block counting once."""
        windows = 0
        start = self.units[0].block_index if self.units else 0
        while chosen := self.window(start):
            windows += 1
            start = chosen[-1].block_index + 1
        return windows

    def unread(self) -> list[int]:
        return [unit.block_index for unit in self.units if unit.block_index not in self.read]

    def show(self, units: list[TextUnit], offset: int = 0) -> str:
        """Spotlight ``units``, the first from character ``offset``, at most
        ``_MAX_UNIT_CHARS`` of each, and record what the model has now seen."""
        lines = []
        for position, unit in enumerate(units):
            start = min(max(offset, 0), len(unit.text)) if position == 0 else 0
            line, end = _render_line(unit, start)
            if start <= self.shown.get(unit.block_index, 0):
                self.shown[unit.block_index] = max(self.shown.get(unit.block_index, 0), end)
            if self.shown.get(unit.block_index, 0) >= len(unit.text):
                self.read.add(unit.block_index)
            lines.append(line)
        return self.spotlight_text("\n".join(lines))

    def spotlight_text(self, body: str) -> str:
        """``body`` inside this session's boundary, which marks it as document text."""
        return f"<{self.boundary}>\n{body}\n</{self.boundary}>"


def _render_line(unit: TextUnit, start: int) -> tuple[str, int]:
    """One block as ``show`` renders it from character ``start``, and where that part ends."""
    text = unit.text
    start = min(max(start, 0), len(text))
    end = min(len(text), start + _MAX_UNIT_CHARS)
    head = "…" if start else ""
    tail = f"…[continues: read_blocks(start={unit.block_index}, offset={end})]" if end < len(text) else ""
    return f"[B{unit.block_index}] {head}{text[start:end]}{tail}", end


class _NerTool(Tool):
    def __init__(self, session: ExtractionSession, tool_name: str, summary: str, detail: str, params: list[ToolParameter]) -> None:
        self._session = session
        self._name = tool_name
        self._summary = summary
        self._detail = detail
        self._params = params

    @property
    def name(self) -> str:
        return self._name

    @property
    def short_description(self) -> str:
        return self._summary

    @property
    def description(self) -> str:
        return self._detail

    @property
    def path(self) -> str:
        return f"/tools/ner/{self._name}"

    @property
    def parameters(self) -> list[ToolParameter]:
        return self._params


class ReadBlocksTool(_NerTool):
    def __init__(self, session: ExtractionSession) -> None:
        super().__init__(
            session,
            "read_blocks",
            "Read a window of document blocks",
            "Return spotlighted block text: at least count blocks from start, then more while "
            "the window stays under about 4,000 characters. A block longer than 3,000 "
            "characters is shown in parts; continue it with the offset its text ends with.",
            [
                ToolParameter("start", ParameterType.INTEGER, "First block index to read", required=True),
                ToolParameter("count", ParameterType.INTEGER, "Fewest blocks to return, max 5", required=False),
                ToolParameter(
                    "offset", ParameterType.INTEGER, "Character offset in the first block, to continue it",
                    required=False,
                ),
            ],
        )

    async def execute(self, start: int = 0, count: int = _WINDOW, offset: int = 0, **_: object) -> ToolOutput:
        start = int(start)
        first_offset = int(offset or 0)
        chosen = self._session.window(start, int(count or _WINDOW), first_offset)
        if not chosen:
            return ToolOutput(success=True, data="No blocks at that index.")
        if chosen[0].block_index != start:
            first_offset = 0
        return ToolOutput(success=True, data=self._session.show(chosen, first_offset))


class SearchDocumentTool(_NerTool):
    def __init__(self, session: ExtractionSession) -> None:
        super().__init__(
            session,
            "search_document",
            "Find a literal string in this record",
            "Case-insensitive literal search. Returns at most 20 hits. Regex is not accepted.",
            [ToolParameter("text", ParameterType.STRING, "Literal text to find", required=True)],
        )

    async def execute(self, text: str = "", **_: object) -> ToolOutput:
        needle = (text or "").casefold().strip()
        if not needle or len(needle) > 200:
            return ToolOutput(success=False, error="text must be 1-200 characters")
        hits: list[str] = []
        for unit in self._session.units:
            folded = unit.text.casefold()
            at = folded.find(needle)
            if at < 0:
                continue
            snippet = unit.text[max(0, at - 40) : at + len(needle) + 40]
            hits.append(f"B{unit.block_index} @{at}: {snippet}")
            if len(hits) >= _MAX_HITS:
                break
        return ToolOutput(success=True, data=hits or "No matches.")


class NormalizeValueTool(_NerTool):
    def __init__(self, session: ExtractionSession) -> None:
        super().__init__(
            session,
            "normalize_value",
            "Normalize a date, amount or quantity",
            "Deterministic normalizer. Do not invent the numeric value yourself.",
            [
                ToolParameter("kind", ParameterType.STRING, "Entity kind", required=True),
                ToolParameter("text", ParameterType.STRING, "Surface form", required=True),
                ToolParameter("block", ParameterType.INTEGER, "Block index", required=True),
            ],
        )

    async def execute(self, kind: str = "", text: str = "", block: int = 0, **_: object) -> ToolOutput:
        parsed = parse_wire_kind(kind)
        if parsed is None:
            return ToolOutput(success=False, error=f"unknown kind {kind}")
        value = normalize_value(parsed, text, self._session.norm_ctx)
        if value is None:
            return ToolOutput(success=True, data={"ok": False, "kind": parsed.value})
        return ToolOutput(success=True, data={"ok": True, "kind": parsed.value, "value": value.model_dump(mode="json")})


class SubmitEntitiesTool(_NerTool):
    def __init__(self, session: ExtractionSession) -> None:
        super().__init__(
            session,
            "submit_entities",
            "Submit entities found in the blocks",
            "Each item needs verbatim text, a block index and a kind. Invalid items are reported; valid ones are kept.",
            [
                ToolParameter(
                    "entities",
                    ParameterType.ARRAY,
                    f"Entities in order of appearance, at most {_MAX_SUBMIT_ITEMS}",
                    required=True,
                    items=wire_item_schema(),
                ),
            ],
        )

    async def execute(self, entities: list | str | None = None, **_: object) -> ToolOutput:
        items = _submitted_items(entities)
        if items is None:
            return ToolOutput(success=False, error="entities must be a list of objects")
        if len(items) > _MAX_SUBMIT_ITEMS:
            return ToolOutput(success=False, error=f"at most {_MAX_SUBMIT_ITEMS} entities per call")
        before = len(self._session.staging)
        results = await self._session.staged(lambda: [_stage_raw(self._session, i, raw) for i, raw in enumerate(items)])
        self._session.submits += 1
        if len(items) == _MAX_SUBMIT_ITEMS:
            self._session.full_submits += 1
        if len(self._session.staging) == before:
            self._session.staging.empty_submits += 1
        else:
            self._session.staging.empty_submits = 0
        # Only what the model can act on: an accepted item needs no answer.
        data: dict[str, object] = {"accepted": sum(1 for result in results if result["status"] == "accepted")}
        problems = [result for result in results if result["status"] != "accepted"]
        if problems:
            data["rejected"] = problems
        if self._session.staging.empty_submits >= 2:
            data["note"] = "No new entities. Call finish if the document is covered."
        return ToolOutput(success=True, data=data)


def _stage_raw(session: ExtractionSession, index: int, raw: object) -> dict:
    # Each item stands alone: one malformed entity must not cost the others.
    try:
        item = WireEntity.model_validate(raw)
    except ValidationError as exc:
        session.staging.submitted += 1
        session.staging.reject("schema")
        return {"index": index, "status": "rejected", "error": _schema_error(exc)}
    return stage_wire_entity(session, index, item)


def _submitted_items(entities: object) -> list | None:
    if entities is None:
        return []
    if isinstance(entities, str):
        # Some models send the array as a JSON string.
        try:
            entities = json.loads(entities)
        except ValueError:
            return None
    return entities if isinstance(entities, list) else None


def _schema_error(exc: ValidationError) -> str:
    problems = [
        f"{'.'.join(str(part) for part in error['loc']) or 'item'}: {error['msg']}" for error in exc.errors()[:3]
    ]
    return "; ".join(problems)[:300]


def stage_wire_entity(session: ExtractionSession, index: int, item: WireEntity, *, extractor: str = "agent") -> dict:
    """Ground one wire item into the staging set. Invalid items are counted, not stored."""
    session.staging.submitted += 1
    kind = parse_wire_kind(item.kind)
    if kind is None or kind not in session.enabled:
        session.staging.reject("kind_disabled")
        return {"index": index, "status": "rejected", "error": "kind is not enabled"}
    if is_suppressed_secret(item.text):
        session.staging.reject("secret")
        return {"index": index, "status": "rejected", "error": "value must not be stored"}
    spec = spec_for(kind)
    aligned = session.grounder.align(item.block, item.text, fuzzy=spec is not None and spec.source is EntitySource.SEMANTIC)
    retry_key = (item.block, item.text.casefold(), kind.value)
    if aligned is None:
        tries = session.retries.get(retry_key, 0) + 1
        session.retries[retry_key] = tries
        session.staging.reject("not_in_block")
        if tries > _MAX_RETRIES:
            return {"index": index, "status": "dropped", "error": "not found in block"}
        nearest = session.grounder.nearest(item.block, item.text)
        hint = f" nearest: {nearest!r}" if nearest else ""
        return {"index": index, "status": "rejected", "error": f"{item.text!r} not in B{item.block}.{hint}"}
    start, end, surface, grounding = aligned
    if spec is not None and spec.source is EntitySource.SEMANTIC and (reason := name_rejection(kind, surface)):
        session.staging.drop(f"not_a_name:{reason}")
        return {
            "index": index,
            "status": "dropped",
            "error": f"{surface!r} is not a {kind.value} name ({reason}); do not resubmit it",
        }
    if spec is not None and spec.source is EntitySource.VALUE and normalize_value(kind, surface, session.norm_ctx) is None:
        session.staging.reject("unparsed")
        return {
            "index": index,
            "status": "rejected",
            "error": f"{surface!r} does not state a {kind.value}; submit the text that states it",
        }
    unit = next((u for u in session.units if u.block_index == item.block), None)
    session.staging.upsert(
        RawMention(
            kind=kind,
            surface=surface,
            block_index=item.block,
            block_id=unit.block_id if unit else "",
            char_start=start,
            char_end=end,
            extractor=extractor,  # type: ignore[arg-type]
            evidence_score=1.0 if grounding == "exact" else 0.7,
            grounding=grounding,  # type: ignore[arg-type]
        )
    )
    return {"index": index, "status": "accepted"}


class FinishTool(_NerTool):
    def __init__(self, session: ExtractionSession) -> None:
        super().__init__(
            session,
            "finish",
            "Finish extraction",
            "Call when every block has been read. Refused while unread blocks remain.",
            [ToolParameter("reason", ParameterType.STRING, "Why extraction is complete", required=True)],
        )

    @property
    def tags(self) -> list[Tag]:
        return [TAG_LIFECYCLE_TERMINAL]

    async def execute(self, reason: str = "", **_: object) -> ToolOutput:
        unread = self._session.unread()
        if unread:
            preview = [
                f"{index} (from offset {self._session.shown[index]})" if self._session.shown.get(index) else str(index)
                for index in unread[:8]
            ]
            return ToolOutput(
                success=False,
                error=f"blocks {', '.join(preview)} not yet reviewed",
            )
        self._session.finished = True
        return ToolOutput(success=True, data={"reason": reason or "done", "accepted": len(self._session.staging)})

    def extract_outcome(self, tr: CoreToolResult, call: ToolCall, fallback_text: str) -> TaskCompletionOutcome:
        return TaskCompletionOutcome(
            task_done=True,
            final_output={"accepted": len(self._session.staging), "reason": "finish_ok"},
        )


def build_tools(session: ExtractionSession) -> list[Tool]:
    return [
        ReadBlocksTool(session),
        SearchDocumentTool(session),
        NormalizeValueTool(session),
        SubmitEntitiesTool(session),
        FinishTool(session),
    ]
