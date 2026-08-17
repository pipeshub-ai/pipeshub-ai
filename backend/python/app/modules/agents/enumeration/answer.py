"""Compose an enumeration answer from the record set, with citations attached.

The point of this module is that the answer is *computed*, not generated. A
model asked to count from twenty retrieved passages produces a confident number
with no passage to attribute it to, and then drops the citations from the rest
of the answer along with it (#2975). Here the records are enumerated first and
the citation for each one is minted as it is counted, so attribution is a
by-product of the computation rather than a request made to the model.

`build_enumeration_answer` returns the composed text alongside the record
structures the citation resolver needs. Both matter: a `[source](refN)` whose
record never reaches `final_results` / `virtual_record_id_to_result` is dropped
during finalisation (`utils/citations.py`, "DROPPED record-page citation").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_LISTED = 200


@dataclass
class EnumerationAnswer:
    """Composed answer plus everything needed to resolve its citations."""

    text: str
    final_results: list[dict[str, Any]] = field(default_factory=list)
    virtual_record_id_to_result: dict[str, Any] = field(default_factory=dict)
    tool_records: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0
    is_empty: bool = True


def _record_url(record: dict[str, Any]) -> str:
    """The record landing URL is what the citation resolver matches on when a
    citation refers to a whole record rather than a block inside one."""
    web_url = record.get("webUrl") or record.get("web_url") or ""
    record_id = record.get("id") or record.get("record_id") or ""
    if web_url:
        return web_url
    return f"/record/{record_id}" if record_id else ""


def compose_text(
    rows: list[tuple[str, str, str | None]],
    total: int,
    *,
    scope: str | None = None,
) -> str:
    """Render the answer. `rows` is (record_name, ref, summary).

    The count is stated without a citation because it is derived from the record
    set rather than from any one document, and then every record it counted is
    cited individually. That is the shape the count and its evidence should
    have: the total is ours, the contents are theirs.
    """
    if total == 0:
        where = f" matching {scope}" if scope else ""
        return f"There are no documents{where} that you have access to."

    noun = "document" if total == 1 else "documents"
    where = f" matching {scope}" if scope else ""
    lines = [f"There are {total} {noun}{where}:", ""]
    for name, ref, summary in rows:
        detail = f" — {summary.strip()}" if summary else ""
        lines.append(f"- **{name}**{detail} [source]({ref})")
    if total > len(rows):
        lines.append("")
        lines.append(f"({total - len(rows)} more not listed above.)")
    return "\n".join(lines)


async def build_enumeration_answer(
    *,
    accessible: dict[str, str],
    record_lookup: Any,
    ref_mapper: Any,
    org_id: str,
    scope: str | None = None,
    limit: int = MAX_LISTED,
) -> EnumerationAnswer:
    """Enumerate the accessible record set and cite each record as it is counted.

    `accessible` is the permission-filtered virtualRecordId -> recordId map, so
    the count is exhaustive over what this person may see rather than over a
    top-k sample. `record_lookup(vrid, record_id)` returns the record dict, or
    None when the record cannot be resolved — an unresolvable record is skipped
    rather than counted, because a count that includes rows nobody can cite is
    the failure this module exists to remove.
    """
    total = 0
    rows: list[tuple[str, str, str | None]] = []
    final_results: list[dict[str, Any]] = []
    vr_map: dict[str, Any] = {}
    tool_records: list[dict[str, Any]] = []

    for vrid, record_id in accessible.items():
        record = await record_lookup(vrid, record_id)
        if not record:
            continue
        total += 1
        if len(rows) >= limit:
            continue

        url = _record_url(record)
        if not url:
            continue
        ref = ref_mapper.get_or_create_ref(url)
        name = record.get("record_name") or record.get("recordName") or record_id
        summary = record.get("summary") or (record.get("semantic_metadata") or {}).get("summary")
        rows.append((name, ref, summary))

        vr_map[vrid] = record
        tool_records.append(record)
        # The resolver matches a record-level citation by record id, so the
        # record has to be reachable from both maps it consults.
        # The citation schema requires orgId, mimeType, recordId, recordName and
        # origin; a citation missing any of them fails validation on save and
        # takes the whole conversation with it.
        final_results.append({
            "content": summary or name,
            "metadata": {
                "recordId": record.get("id") or record_id,
                "recordName": name,
                "virtualRecordId": vrid,
                "orgId": org_id,
                "webUrl": url,
                "mimeType": record.get("mime_type") or "text/plain",
                "origin": record.get("origin") or "UPLOAD",
                "recordType": record.get("record_type") or "FILE",
                "connector": record.get("connector_name") or "KB",
            },
        })

    return EnumerationAnswer(
        text=compose_text(rows, total, scope=scope),
        final_results=final_results,
        virtual_record_id_to_result=vr_map,
        tool_records=tool_records,
        total=total,
        is_empty=total == 0,
    )
