"""Builders for compiler tests: history items as the kernel renders them."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.context.types import HistoryItem, HostBands, ToolEntry
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.labels import Labels
from harness_core.kernel.core.messages import (
    AssistantMessage,
    TextPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from harness_core.kernel.core.tool_schema import ToolSchema

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import JsonValue


def label(integrity: str, *sources: str, readers: JsonValue = "org") -> Labels:
    doc: dict[str, JsonValue] = {"integrity": integrity}
    if sources:
        doc["confidentiality"] = {"readers": readers, "sources": list(sources)}
    return validate_contract(Labels, doc)


def tool(name: str) -> ToolEntry:
    return ToolEntry(
        tool_id=f"inst/{name}",
        schema=ToolSchema(name=name, description=f"{name} tool", input_schema={"type": "object", "properties": {}}),
    )


def host(*names: str, tail: Sequence[str] = (), system: str = "You are the agent.") -> HostBands:
    return HostBands(system=system, tools=tuple(tool(n) for n in names), tail=tuple(tail))


def ask(seq: int, text: str, *, own: bool = False) -> HistoryItem:
    return HistoryItem(first_seq=seq, last_seq=seq, messages=(UserMessage(content=text),), own_input=own)


def answer(seq: int, text: str) -> HistoryItem:
    return HistoryItem(first_seq=seq, last_seq=seq, messages=(AssistantMessage(content=[TextPart(text=text)]),))


def called(
    seq: int, turn: int, *results: str, labels: Sequence[Labels | None] = (), last_seq: int | None = None
) -> HistoryItem:
    """An assistant turn calling ``len(results)`` tools, with the results (outcomes at the following seqs)."""
    calls = [ToolCall(id=f"c{turn}.{i}", name="lookup", arguments={"i": i}) for i in range(len(results))]
    messages = [
        AssistantMessage(content=[TextPart(text=f"turn {turn}")], tool_calls=calls),
        *(ToolMessage(content=r, tool_call_id=c.id) for c, r in zip(calls, results, strict=True)),
    ]
    found = {c.id: lbl for c, lbl in zip(calls, labels, strict=False) if lbl is not None}
    return HistoryItem(
        first_seq=seq,
        last_seq=last_seq if last_seq is not None else seq + len(results),
        messages=tuple(messages),
        result_labels=found,
        invocations={c.id: f"t{turn}.{i}" for i, c in enumerate(calls)},
    )


def session(turns: int, *, start: int = 1, result_chars: int = 180) -> list[HistoryItem]:
    """An input, then ``turns`` tool turns of one result each, then an answer."""
    items = [ask(start, "please help", own=True)]
    seq = start + 1
    for turn in range(1, turns + 1):
        items.append(called(seq, turn, (f"result {turn} " * result_chars)[:result_chars]))
        seq += 2
    items.append(answer(seq, "done"))
    return items
