"""Compose a census answer, with each record cited as it is counted.

The answer is computed rather than generated. A model asked to count from
twenty retrieved passages produces a number with no passage to attribute it to,
and drops the citations from the rest of the answer along with it (#2975). Here
the records are enumerated first and a citation is minted for each one, so
attribution is a by-product of the computation.

Two details matter and are easy to get wrong:

* The citation must be minted against the record landing URL, `/record/{id}`.
  The resolver indexes results by `block_web_url` and, failing that, extracts a
  record id from a `/record/...` path (`utils/citations.py`). A connector's
  external `webUrl` matches neither, so minting against it produces a link the
  resolver silently strips.
* The total is the size of the permission-filtered record set, not the number of
  rows listed. Counting only the hydrated rows would under-report a large corpus.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_LISTED = 200


@dataclass
class EnumerationAnswer:
    """Composed answer plus everything the citation resolver needs."""

    text: str
    final_results: list[dict[str, Any]] = field(default_factory=list)
    virtual_record_id_to_result: dict[str, Any] = field(default_factory=dict)
    tool_records: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0
    listed: int = 0
    is_empty: bool = True


def record_landing_url(record_id: str) -> str:
    """The only URL form the citation resolver can match for a whole record."""
    return f"/record/{record_id}"


def compose_text(rows: list[tuple[str, str, str | None]], total: int) -> str:
    """Render the answer. `rows` is (record_name, ref, summary).

    The count carries no citation because it is derived from the record set
    rather than stated in any document; every record it counted is then cited
    individually.
    """
    if total == 0:
        return "There are no documents that you have access to."

    noun = "document" if total == 1 else "documents"
    lines = [f"There are {total} {noun}:", ""]
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
    limit: int = MAX_LISTED,
) -> EnumerationAnswer:
    """Count the permission-filtered record set and cite each listed record.

    `accessible` is the virtualRecordId -> recordId map, so the total is exact
    over what this person may see rather than over a top-k sample. Only the
    first `limit` records are hydrated and listed; the rest are counted and
    reported as a remainder, which keeps a large corpus from turning one
    question into thousands of reads.

    Iteration is sorted so repeated asks return the same answer — a map's order
    is not a promise.
    """
    ordered = sorted(accessible.items())
    total = len(ordered)
    rows: list[tuple[str, str, str | None]] = []
    final_results: list[dict[str, Any]] = []
    vr_map: dict[str, Any] = {}
    tool_records: list[dict[str, Any]] = []

    for vrid, record_id in ordered[:limit]:
        record = await record_lookup(vrid, record_id)
        if not record:
            # Unresolvable rows are not listed. They stay in the total, because
            # the total is what the permission filter returned and dropping them
            # would silently under-count.
            continue

        rid = record.get("id") or record_id
        url = record_landing_url(rid)
        ref = ref_mapper.get_or_create_ref(url)
        name = record.get("record_name") or rid
        summary = record.get("summary")
        rows.append((name, ref, summary))

        vr_map[vrid] = record
        tool_records.append(record)
        # `block_web_url` is the key the resolver indexes on; `metadata.webUrl`
        # keeps the connector's external link for the chip target.
        final_results.append({
            "content": summary or name,
            "block_web_url": url,
            "metadata": {
                "recordId": rid,
                "recordName": name,
                "virtualRecordId": vrid,
                "orgId": org_id,
                "webUrl": record.get("webUrl") or url,
                # The citation schema rejects a document missing any of these,
                # and a rejected citation fails the whole conversation save.
                "mimeType": record.get("mime_type") or "text/plain",
                "origin": record.get("origin") or "UPLOAD",
                "recordType": record.get("record_type") or "FILE",
                "connector": record.get("connector_name") or "KB",
            },
        })

    return EnumerationAnswer(
        text=compose_text(rows, total),
        final_results=final_results,
        virtual_record_id_to_result=vr_map,
        tool_records=tool_records,
        total=total,
        listed=len(rows),
        is_empty=total == 0,
    )
