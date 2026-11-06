"""What the model has actually been shown this request: each record's name and
the passages of it that reached the model, from search and prefetch blocks and
from fetches (only the blocks a fetch rendered, not the whole stored record).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RENDER_OUTCOMES_KEY
from app.agents.agent_loop.coverage.facets import tokens
from app.modules.retrieval.context.units import unit_children
from app.utils.chat_helpers import is_base64_image

_TEXT_KEYS = ("row_natural_language_text", "text", "description")
_MAX_REF_NAME_CHARS = 120


@dataclass
class EvidenceRecord:
    virtual_record_id: str
    record_id: str | None
    name: str
    passages: list[str] = field(default_factory=list[str])
    fetched: bool = False
    complete: bool = False
    blocks: dict[int, str] = field(default_factory=dict[int, str])
    """Shown text by block index, for matching the blocks a draft cites."""

    def add(self, text: str, block_index: object = None) -> None:
        text = text.strip()
        if not text:
            return
        if isinstance(block_index, int) and not isinstance(block_index, bool):
            self.blocks.setdefault(block_index, text)
        if text not in self.passages:
            self.passages.append(text)

    def block_texts(self, indices: Collection[int]) -> list[str]:
        """The shown text of these blocks, in block order."""
        return [self.blocks[index] for index in sorted(set(indices)) if index in self.blocks]

    def cited_passages(self, indices: Collection[int]) -> list[str]:
        """The shown text of the cited blocks; every shown passage when the
        citation names no block, or none of the named blocks was shown."""
        return self.block_texts(indices) or list(self.passages)


@dataclass
class Evidence:
    records: list[EvidenceRecord] = field(default_factory=list["EvidenceRecord"])

    def texts(self) -> list[str]:
        return [part for record in self.records for part in (record.name, *record.passages) if part]

    def read_records(self) -> list[EvidenceRecord]:
        """Records whose own text reached the model (not just a listed name)."""
        return [record for record in self.records if record.passages]

    def cited_texts(self, cited: Mapping[str, Collection[int]]) -> list[str]:
        """The passages a draft cites, given cited block indices per record id."""
        return [
            passage
            for record in self.records if record.record_id and record.record_id in cited
            for passage in record.cited_passages(cited[record.record_id])
        ]

    def label_of(self, record_id: str | None) -> str | None:
        for position, record in enumerate(self.records, start=1):
            if record_id and record.record_id == record_id:
                return f"R{position}"
        return None

    def by_label(self, label: str) -> EvidenceRecord | None:
        label = label.strip().upper().lstrip("[").rstrip("]")
        if not label.startswith("R") or not label[1:].isdigit():
            return None
        position = int(label[1:])
        return self.records[position - 1] if 0 < position <= len(self.records) else None


def _as_mapping(value: object) -> Mapping[str, Any] | None:
    return cast("Mapping[str, Any]", value) if isinstance(value, Mapping) else None


def _as_list(value: object) -> list[object]:
    return list(cast("Iterable[object]", value)) if isinstance(value, (list, tuple)) else []


def _content_text(value: object) -> str:
    if isinstance(value, str):
        return "" if is_base64_image(value) else value
    mapping = _as_mapping(value)
    if mapping is not None:
        for key in _TEXT_KEYS:
            text = mapping.get(key)
            if isinstance(text, str) and text:
                return text
    return ""


def record_ref(record_id: str | None, name: str) -> str:
    """How a request to the model names a record: by id, with its name as one
    quoted line, so a title cannot read as an instruction of the request."""
    shown = " ".join(name.replace('"', "'").split())[:_MAX_REF_NAME_CHARS]
    if record_id:
        return f'record {record_id} ("{shown}")' if shown else f"record {record_id}"
    return f'"{shown}"' if shown else "a record"


def _name(record: Mapping[str, Any] | None) -> str:
    if not record:
        return ""
    return str(record.get("record_name") or record.get("recordName") or "")


def _record_blocks(record: Mapping[str, Any] | None) -> dict[int, Mapping[str, Any]]:
    containers = _as_mapping((record or {}).get("block_containers"))
    blocks: dict[int, Mapping[str, Any]] = {}
    for item in _as_list(containers.get("blocks") if containers is not None else None):
        block = _as_mapping(item)
        if block is not None and isinstance(block.get("index"), int):
            blocks[block["index"]] = block
    return blocks


def collect_evidence(tool_state: Mapping[str, Any]) -> Evidence:
    """Every record the request has shown the model, with the passages it saw."""
    records_map: Mapping[str, Any] = tool_state.get("virtual_record_id_to_result") or {}
    by_vrid: dict[str, EvidenceRecord] = {}

    def entry(vrid: str) -> EvidenceRecord:
        if vrid not in by_vrid:
            record = _as_mapping(records_map.get(vrid))
            by_vrid[vrid] = EvidenceRecord(
                virtual_record_id=vrid,
                record_id=str(record["id"]) if record and record.get("id") else None,
                name=_name(record),
            )
        return by_vrid[vrid]

    for item in _as_list(tool_state.get("final_results")):
        unit = _as_mapping(item)
        if unit is None or not unit.get("virtual_record_id"):
            continue
        record = entry(str(unit["virtual_record_id"]))
        record.add(_content_text(unit.get("content")), unit.get("block_index"))
        for child in unit_children(unit):
            record.add(_content_text(child.get("content")), child.get("block_index"))

    outcomes = _as_mapping(tool_state.get(FETCH_RENDER_OUTCOMES_KEY)) or {}
    for entries in outcomes.values():
        for item in _as_list(entries):
            outcome = _as_mapping(item)
            vrid = outcome.get("virtualRecordId") if outcome is not None else None
            if outcome is None or not vrid:
                continue
            record = entry(str(vrid))
            record.fetched = True
            record.complete = record.complete or bool(outcome.get("complete"))
            blocks = _record_blocks(_as_mapping(records_map.get(vrid)))
            for index in _as_list(outcome.get("shownBlocks")):
                block = blocks.get(index) if isinstance(index, int) else None
                if block is not None:
                    record.add(_content_text(block.get("data")), index)
    return Evidence(records=list(by_vrid.values()))


def _ranked_passages(record: EvidenceRecord, focus: set[str]) -> list[str]:
    if not focus:
        return record.passages
    return sorted(record.passages, key=lambda passage: -len(tokens(passage) & focus))


def _excerpt(passages: Iterable[str], budget: int, *, verbatim: bool) -> str:
    body = ""
    for passage in passages:
        room = budget - len(body) - (3 if body else 0)
        if room <= 80:
            break
        snippet = passage.strip() if verbatim else " ".join(passage.split())
        body += (" | " if body else "") + (snippet if len(snippet) <= room else snippet[: room - 1] + "…")
    return body


def evidence_digest(
    evidence: Evidence,
    *,
    focus_terms: Iterable[str] = (),
    cited_record_ids: Iterable[str] = (),
    cited_blocks: Mapping[str, Collection[int]] | None = None,
    new_virtual_record_ids: Collection[str] = (),
    per_record_chars: int = 1_800,
    cited_record_chars: int = 6_000,
    total_chars: int = 20_000,
) -> str:
    """The evidence as labelled excerpts (``[R1] name``) for the checker.

    A cited record leads with the blocks the draft cites, verbatim, within
    ``cited_record_chars``; then every record gets up to ``per_record_chars``
    of its other passages, those sharing most words with ``focus_terms``
    first. Cited records come first, then records in
    ``new_virtual_record_ids`` (just found by a search run in code), then
    fetched ones, then records only seen in results."""
    focus: set[str] = set()
    for term in focus_terms:
        focus |= tokens(term)
    blocks_cited = dict(cited_blocks or {})
    cited = set(cited_record_ids) | set(blocks_cited)
    new = set(new_virtual_record_ids)
    order = sorted(
        range(len(evidence.records)),
        key=lambda i: (
            evidence.records[i].record_id not in cited,
            evidence.records[i].virtual_record_id not in new,
            not evidence.records[i].fetched,
            i,
        ),
    )
    parts: list[str] = []
    used = 0
    for i in order:
        record = evidence.records[i]
        if not record.passages:
            continue
        how = "read in full" if record.complete else "partly read" if record.fetched else "blocks from search"
        is_cited = record.record_id in cited
        note = ", cited in the draft" if is_cited else ", new from a search run for you" if (
            record.virtual_record_id in new
        ) else ""
        header = f"[R{i + 1}] {record.name or '(untitled)'} ({how}{note})"
        if is_cited:
            lead = record.block_texts(blocks_cited.get(record.record_id or "", ()))
            rest = [passage for passage in _ranked_passages(record, focus) if passage not in lead]
            body = _excerpt(lead, cited_record_chars, verbatim=True)
            tail = _excerpt(rest, per_record_chars - len(body) - 3, verbatim=False)
            body = f"{body} | {tail}" if body and tail else body or tail
        else:
            body = _excerpt(_ranked_passages(record, focus), per_record_chars, verbatim=False)
        block = f"{header}\n{body}"
        if used + len(block) > total_chars:
            continue
        parts.append(block)
        used += len(block) + 2
    return "\n\n".join(parts)


__all__ = ["Evidence", "EvidenceRecord", "collect_evidence", "evidence_digest", "record_ref"]
