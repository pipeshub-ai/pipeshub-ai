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
from app.modules.named_entities.mentions import RawMention
from app.modules.named_entities.normalizers import normalize_value
from app.modules.named_entities.normalizers.dates import NormalizationContext
from app.modules.named_entities.recognizers.pattern import is_suppressed_secret
from app.modules.named_entities.strategies.agent.staging import StagingSet
from app.modules.named_entities.text import TextUnit
from app.modules.named_entities.wire import (
    WireEntity,
    parse_wire_kind,
    wire_item_schema,
)

if TYPE_CHECKING:
    from collections.abc import Callable

T = TypeVar("T")

_WINDOW = 5
# A window of _WINDOW blocks stays under 16k characters. The model sees the head of
# a longer block; search_document and exact grounding still cover all of it.
_MAX_UNIT_CHARS = 3_000
_MAX_HITS = 20
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
        self.read: set[int] = set()
        self.boundary = secrets.token_hex(4)
        self.seed_count = seed_count
        self.retries: dict[tuple, int] = {}
        self.finished = False
        self._staging_lock = asyncio.Lock()

    async def staged(self, stage: Callable[[], T]) -> T:
        """Run staging off the event loop: fuzzy grounding is CPU work that would
        stall every record on the loop and its lease heartbeats. The lock keeps
        tool calls of one turn, which run concurrently, from staging at once."""
        async with self._staging_lock:
            return await asyncio.to_thread(stage)

    def unread(self) -> list[int]:
        return [unit.block_index for unit in self.units if unit.block_index not in self.read]

    def spotlight(self, units: list[TextUnit]) -> str:
        body = "\n".join(f"[B{unit.block_index}] {_clip(unit.text)}" for unit in units)
        return f"<{self.boundary}>\n{body}\n</{self.boundary}>"


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_UNIT_CHARS else text[:_MAX_UNIT_CHARS] + "…[truncated]"


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
            "Return spotlighted block text. count is at most 5.",
            [
                ToolParameter("start", ParameterType.INTEGER, "First block index to read", required=True),
                ToolParameter("count", ParameterType.INTEGER, "How many blocks, max 5", required=False),
            ],
        )

    async def execute(self, start: int = 0, count: int = _WINDOW, **_: object) -> ToolOutput:
        count = max(1, min(int(count or _WINDOW), _WINDOW))
        start = int(start)
        chosen = [unit for unit in self._session.units if unit.block_index >= start][:count]
        for unit in chosen:
            self._session.read.add(unit.block_index)
        if not chosen:
            return ToolOutput(success=True, data="No blocks at that index.")
        return ToolOutput(success=True, data=self._session.spotlight(chosen))


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
                    "Entities in order of appearance, at most 100",
                    required=True,
                    items=wire_item_schema(),
                ),
            ],
        )

    async def execute(self, entities: list | str | None = None, **_: object) -> ToolOutput:
        items = _submitted_items(entities)
        if items is None:
            return ToolOutput(success=False, error="entities must be a list of objects")
        if len(items) > 100:
            return ToolOutput(success=False, error="at most 100 entities per call")
        before = len(self._session.staging)
        results = await self._session.staged(lambda: [_stage_raw(self._session, i, raw) for i, raw in enumerate(items)])
        if len(self._session.staging) == before:
            self._session.staging.empty_submits += 1
        else:
            self._session.staging.empty_submits = 0
        note = ""
        if self._session.staging.empty_submits >= 2:
            note = " No new entities. Call finish if the document is covered."
        return ToolOutput(success=True, data={"results": results, "note": note})


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


def stage_wire_entity(session: ExtractionSession, index: int, item, *, extractor: str = "agent") -> dict:
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
            normalized_hint=item.normalized,
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
    def tags(self):
        return [TAG_LIFECYCLE_TERMINAL]

    async def execute(self, reason: str = "", **_: object) -> ToolOutput:
        unread = self._session.unread()
        if unread:
            preview = unread[:8]
            return ToolOutput(
                success=False,
                error=f"blocks {preview} not yet reviewed",
            )
        self._session.finished = True
        return ToolOutput(success=True, data={"reason": reason or "done", "accepted": len(self._session.staging)})

    def extract_outcome(self, tr, call, fallback_text: str) -> TaskCompletionOutcome:
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
