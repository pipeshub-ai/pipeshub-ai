"""Citations for what a listing shows about a record.

navigate and lookup_record show records' names, dates and statuses but none of
their content, so an answer built from them ("the latest checklist was created
on Oct 26") had nothing to cite. Each record they list gets a record-level
Citation ID: the citation opens that record and quotes the listing's own facts,
never words from inside the record.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.utils.chat_helpers import (
    CitationRefMapper,
    build_record_page_web_url,
    resolve_frontend_url,
)
from app.utils.citations import LISTED_FACTS_KEY, listing_citation_url

from .views import _compact_date

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.modules.agents.qna.chat_state import ChatState

    from .models import NodeRow

logger = logging.getLogger(__name__)

# Lines of `Record.to_llm_context()` that state facts about the record rather
# than identify it (ids, URLs, MIME type). The dates follow
# `RecordDates.header_lines`, which an uploaded file's header uses too.
_FACT_LINE_PREFIXES = (
    "Created At:", "Last Updated", "Document Date:", "File Last Modified:", "Uploaded At:", "* ",
)


def row_facts(row: NodeRow) -> str:
    """What a listing row states about its record, as the citation shows it."""
    created = _compact_date(row.source_created_at)
    modified = _compact_date(row.source_modified_at)
    facts = [
        f"{'Uploaded' if row.upload_dated else 'Created'} {created}" if created else None,
        f"modified {modified}" if modified and modified != created and not row.upload_dated else None,
        row.context_summary,
    ]
    return " · ".join(fact for fact in facts if fact)


def context_facts(context_block: str | None) -> str:
    """The fact lines of a record's `Record.to_llm_context()` block."""
    lines = (line.strip() for line in (context_block or "").splitlines())
    return " · ".join(line.removeprefix("* ") for line in lines if line.startswith(_FACT_LINE_PREFIXES))


async def cite_listed_records(
    state: ChatState,
    graph_provider: Any,  # noqa: ANN401
    org_id: str,
    facts: Mapping[str, str],
) -> dict[str, str]:
    """Give each record in *facts* (Record ID -> what the listing says about it)
    a Citation ID. Returns Record ID -> Citation ID.

    Only ever called with records the listing already checked the user may
    open. Returns nothing when the records cannot be loaded; the listing is
    then shown without Citation IDs, as before.
    """
    if not facts:
        return {}
    frontend_url = await resolve_frontend_url(state.get("config_service"))
    try:
        docs = await graph_provider.get_records_by_record_ids(list(facts), org_id)
    except Exception:
        logger.warning("Could not load %d listed records to cite", len(facts), exc_info=True)
        return {}

    listed: dict[str, dict[str, Any]] = {}
    for doc in docs or []:
        record_id = doc.get("_key") or doc.get("id")
        if record_id in facts:
            listed[record_id] = {**doc, "id": record_id, LISTED_FACTS_KEY: facts[record_id]}
    urls = {rid: listing_citation_url(build_record_page_web_url(frontend_url, rid)) for rid in listed}
    listed = {rid: doc for rid, doc in listed.items() if urls[rid]}
    if not listed:
        return {}

    ref_mapper = state.get("citation_ref_mapper")
    if not isinstance(ref_mapper, CitationRefMapper):
        ref_mapper = state["citation_ref_mapper"] = CitationRefMapper()
    existing = state.get("tool_records")
    existing = existing if isinstance(existing, list) else []
    # A record listed again replaces its earlier listing: the newest facts are
    # the ones shown. One that states nothing new (a name-only list_files row, or
    # page 2 of a navigate, which has no header) keeps the facts its Citation ID
    # was first given for, since that ID is the same.
    for r in existing:
        rid = r.get("id") if isinstance(r, dict) else None
        if rid in listed and r.get(LISTED_FACTS_KEY) and not listed[rid][LISTED_FACTS_KEY]:
            listed[rid][LISTED_FACTS_KEY] = r[LISTED_FACTS_KEY]
    state["tool_records"] = [
        r for r in existing if not (isinstance(r, dict) and LISTED_FACTS_KEY in r and r.get("id") in listed)
    ] + list(listed.values())
    return {rid: ref_mapper.get_or_create_ref(urls[rid]) for rid in listed}
