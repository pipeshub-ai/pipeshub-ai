"""An engine run's full-record read (S1c): today's fetch, limited to records the run's own calls were shown.

Today's loop grants ``knowledgegraph__fetch_record`` once a knowledge tool has
put records in the request's state (``hooks/citations.py``). An engine gets the
same tool, in its grant from the start (it lists its tools once, at connect),
wrapped so that it reads only records shown to it: the ids the run's knowledge
tools surfaced (``known_record_ids`` and the record map). Everything after that
check is today's: the reader resolves the id (the record map, or the user's
access check for an id the map lacks), renders the blocks under the run's
``CitationRefMapper`` and records what was fetched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast, override

from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RECORD_TOOL_NAME
from app.utils.chat_helpers import RecordIdShortener
from harness_core.kernel.core.messages import TextPart
from harness_core.tools.base import Tag, Tool, ToolOutput, ToolParameter

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from harness_core.logs import HarnessLog

NOT_SHOWN = "record_not_shown"


def surfaced_record_ids(state: Mapping[str, Any]) -> frozenset[str]:
    """Record ids the request's knowledge tools surfaced to any agent of the request."""
    known: object = state.get("known_record_ids")
    records: object = state.get("virtual_record_id_to_result")
    ids = {str(i) for i in cast("set[object]", known)} if isinstance(known, set) else set[str]()
    if isinstance(records, dict):
        for record in cast("dict[str, object]", records).values():
            if isinstance(record, dict) and (rid := cast("dict[str, object]", record).get("id")):
                ids.add(str(rid))
    return frozenset(ids)


def _shortener(state: Mapping[str, Any]) -> RecordIdShortener | None:
    """The request's record-id shortener when it has one (an opt-in experiment); never minted here."""
    shortener = state.get("record_id_shortener")
    return shortener if isinstance(shortener, RecordIdShortener) else None


def resolve_record_label(state: Mapping[str, Any], record_id: str) -> str:
    """A short label (``R3``) the request's shortener issued, as its full record id; anything else unchanged."""
    shortener = _shortener(state)
    return shortener.resolve(record_id) if shortener is not None else record_id


def _refused_note(ids: list[str]) -> str:
    named = ", ".join(f"'{i}'" for i in ids)
    return (
        f"Not fetched ({NOT_SHOWN}): {named}. Only records your own knowledge tool calls in this task returned "
        "can be read; search for the record first, then fetch it by the Record ID shown."
    )


class EngineRecordFetchTool(Tool):
    """``reader`` (the request's ``knowledgegraph__fetch_record``) for the ids ``shown()`` holds; the rest refused."""

    def __init__(
        self,
        reader: Tool,
        shown: Callable[[], frozenset[str]],
        resolve: Callable[[str], str],
        log: HarnessLog,
    ) -> None:
        self._reader = reader
        self._shown = shown
        self._resolve = resolve
        self._log = log

    @property
    @override
    def name(self) -> str:
        return FETCH_RECORD_TOOL_NAME

    @property
    @override
    def short_description(self) -> str:
        return self._reader.short_description

    @property
    @override
    def description(self) -> str:
        return self._reader.description

    @property
    @override
    def path(self) -> str:
        return self._reader.path

    @property
    @override
    def display_name(self) -> str | None:
        return self._reader.display_name

    @property
    @override
    def tags(self) -> list[Tag]:
        return self._reader.tags

    @property
    @override
    def parameters(self) -> list[ToolParameter]:
        return self._reader.parameters

    @override
    def validate(self, kwargs: dict[str, Any]) -> None:
        self._reader.validate(kwargs)

    @override
    async def execute(self, **kwargs: Any) -> ToolOutput:
        raw: object = kwargs.get("record_ids") or []
        ids = [str(i) for i in (cast("list[object]", raw) if isinstance(raw, list) else [raw])]
        shown = self._shown()
        allowed = [i for i in ids if self._resolve(i) in shown]
        refused = [i for i in ids if i not in allowed]
        if refused:
            self._log.warning("engine.fetch_refused", reasonCode=NOT_SHOWN, refused=len(refused), requested=len(ids))
        if ids and not allowed:
            return ToolOutput(success=False, error=_refused_note(refused))
        output = await self._reader.execute(**{**kwargs, "record_ids": allowed})
        if refused:
            note = "\n\n" + _refused_note(refused)
            data: object = output.data
            if not output.success:
                output.error = (output.error or "") + note
            elif isinstance(data, list):
                output.data = [*cast("list[object]", data), TextPart(text=note)]
            else:
                output.data = f"{data}{note}"
        return output


__all__ = [
    "NOT_SHOWN",
    "EngineRecordFetchTool",
    "resolve_record_label",
    "surfaced_record_ids",
]
