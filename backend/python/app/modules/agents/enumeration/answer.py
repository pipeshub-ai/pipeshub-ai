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

# How many records the answer will name. A census over a large corpus is a
# number plus a way to narrow it, not a wall of rows: at 200 rows with summaries
# the answer runs past 50,000 characters, which nobody reads and which rides
# along in the conversation history on every following turn.
MAX_LISTED = 50

# Summaries are what make a row long, so they are only worth including while
# the whole list is still readable.
SUMMARY_LIMIT = 20


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


def compose_text(
    rows: list[tuple[str, str, str | None]],
    total: int,
    *,
    beyond_cap: int = 0,
    unreadable: int = 0,
    scoped: bool = False,
    with_summaries: bool = True,
) -> str:
    """Render the answer. `rows` is (record_name, ref, summary).

    The count carries no citation because it is derived from the record set
    rather than stated in any document; every record it counted is then cited
    individually.

    The two reasons a record can be counted but not listed are reported
    separately, because they mean different things to the reader. Being past
    the listing cap says "ask for more"; failing to load says "something is
    wrong with that record", and describing the second as the first would send
    someone looking for a page that does not exist.
    """
    # A count taken over a narrowed set has to say so. "There are 3 documents"
    # reads as a statement about the whole corpus, and would be wrong by orders
    # of magnitude for someone who scoped the question to one knowledge base.
    where = " in the sources you selected" if scoped else ""
    if total == 0:
        if scoped:
            return "There are no documents in the sources you selected."
        return "There are no documents that you have access to."

    noun = "document" if total == 1 else "documents"
    lines = [f"There are {total} {noun}{where}:", ""]
    for name, ref, summary in rows:
        detail = f" — {summary.strip()}" if (with_summaries and summary) else ""
        lines.append(f"- **{name}**{detail} [source]({ref})")
    if beyond_cap > 0 or unreadable > 0:
        lines.append("")
    if beyond_cap > 0:
        lines.append(
            f"({beyond_cap} more not listed above. Ask about a topic, a "
            f"department or a date range to narrow this down.)"
        )
    if unreadable > 0:
        plural = "record" if unreadable == 1 else "records"
        lines.append(
            f"({unreadable} {plural} could not be read and are not listed, "
            f"but are included in the total.)"
        )
    return "\n".join(lines)


async def build_enumeration_answer(
    *,
    accessible: dict[str, str],
    record_lookup: Any,
    ref_mapper: Any,
    org_id: str,
    limit: int = MAX_LISTED,
    scoped: bool = False,
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
    page = ordered[:limit]
    beyond_cap = total - len(page)
    rows: list[tuple[str, str, str | None]] = []
    final_results: list[dict[str, Any]] = []
    vr_map: dict[str, Any] = {}
    tool_records: list[dict[str, Any]] = []

    for vrid, record_id in page:
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
        text=compose_text(
            rows, total, beyond_cap=beyond_cap, unreadable=len(page) - len(rows),
            scoped=scoped, with_summaries=total <= SUMMARY_LIMIT,
        ),
        final_results=final_results,
        virtual_record_id_to_result=vr_map,
        tool_records=tool_records,
        total=total,
        listed=len(rows),
        is_empty=total == 0,
    )
