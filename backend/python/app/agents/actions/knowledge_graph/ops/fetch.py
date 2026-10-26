"""Knowledge graph fetch_record operation.

The execution body for ``knowledgegraph__fetch_record``. The tool itself is
``_FetchFullRecordTool`` in ``hooks/citations.py``, which owns registration,
the dynamic grant, and where the returned ref mapper gets stashed; this module
owns what a fetch actually does. Keeping the two apart is what lets the tool be
built fresh per call from live ``tool_state`` without duplicating the body.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any

from app.agents.actions.knowledge_graph.ops.scope import ids_outside_scope
from app.modules.retrieval.selection_scope import connection_sources_of

if TYPE_CHECKING:
    from app.agent_loop_lib.tools.base import ToolOutput
    from app.agents.agent_loop.context import AgentContext
    from app.utils.chat_helpers import RecordIdShortener

logger = logging.getLogger(__name__)

_DEFAULT_FULL_RECORD_MAX_BLOCKS = 200

FETCH_RECORD_TOOL_NAME = "knowledgegraph__fetch_record"

DEFAULT_FETCH_REASON = "Fetching full record content for comprehensive answer"

# Records this request has shown by relevance. An explicit `start_block=0` on
# one of them is the model following the "read it in order" hint; on any other
# record it is the default some models send on every call.
FETCH_SELECTED_RECORDS_KEY = "fetch_selected_records"

_CITATION_RULE = (
    "\n\nCite facts from the above using each block's `[refN]` id "
    "as a markdown link, e.g. [source](ref2). Do NOT use external URLs as citations."
)

# Below this, a record's share of a multi-record fetch holds a header and a
# sliver of text -- too little to read, enough to pass for "read". Such a
# record is named as unread instead.
_MIN_RECORD_SHARE_CHARS = 2_000

_MAX_ENTITIES_PER_RECORD = 20


async def _named_entity_note(context: "AgentContext", records: list[dict[str, Any]]) -> str:
    """Entities on the records this fetch reads. Omitted when the flag is off."""
    tool_state = getattr(context, "tool_state", None) or {}
    graph = tool_state.get("graph_provider")
    if graph is None or not records:
        return ""
    try:
        from app.services.featureflag.platform_settings import (
            is_named_entity_extraction_enabled,
        )

        if not await is_named_entity_extraction_enabled(tool_state.get("config_service")):
            return ""
    except Exception:
        return ""
    record_ids = [str(record["id"]) for record in records if record.get("id")]

    async def _entities(record_id: str) -> list[dict[str, Any]] | None:
        try:
            return await graph.get_named_entities_for_record(record_id)
        except Exception:
            logger.warning("named entities unavailable for record %s", record_id, exc_info=True)
            return None

    found = await asyncio.gather(*(_entities(record_id) for record_id in record_ids))
    lines: list[str] = []
    for record_id, entities in zip(record_ids, found):
        shown = []
        for entity in (entities or [])[:_MAX_ENTITIES_PER_RECORD]:
            kind = entity.get("kind") or ""
            name = entity.get("name") or ""
            if kind and name:
                shown.append(f"{kind}:{name}")
        if shown:
            lines.append(f"Named entities on {record_id}: " + ", ".join(shown))
    if not lines:
        return ""
    return "\n" + "\n".join(lines) + "\n"


def _requested_max_blocks(raw: object) -> int | None:
    """The model's `max_blocks` as a positive int, or None. It is model input:
    a numeric string is accepted, anything else means "not set"."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = int(float(raw))
    except (TypeError, ValueError, OverflowError):
        return None
    return value if value > 0 else None


def resolve_block_cap(requested_max: int | None) -> int:
    """Resolve the effective block cap for a fetch.

    Deliberately not widened from the model's context window: that reports 128k
    for unknown/local models, far too optimistic for a small LLM. The size of a
    fetch is bounded by `RenderBudget`'s character allowance instead — a block
    count cannot bound content when one block can be an entire table.
    """
    env_raw = os.getenv("PIPESHUB_FULL_RECORD_MAX_BLOCKS", "")
    try:
        env_cap = int(env_raw) if env_raw.strip() else _DEFAULT_FULL_RECORD_MAX_BLOCKS
        if env_cap <= 0:
            env_cap = _DEFAULT_FULL_RECORD_MAX_BLOCKS
    except ValueError:
        logger.warning(
            "Invalid PIPESHUB_FULL_RECORD_MAX_BLOCKS=%r, using %d",
            env_raw, _DEFAULT_FULL_RECORD_MAX_BLOCKS,
        )
        env_cap = _DEFAULT_FULL_RECORD_MAX_BLOCKS
    if requested_max is not None and requested_max > 0:
        return min(env_cap, requested_max)
    return env_cap


# What to tell the model about ids that produced nothing. Grouped by reason so
# "try again shortly" is not buried among ids that will never resolve.
_UNAVAILABLE_PHRASING = {
    "not_indexed_yet": "exists, but its content is not indexed yet",
    "storage_error": "could not be read right now (storage error) — retrying may work",
    "not_available": "not available",
}


def _unavailable_note(
    not_available: list[str],
    reasons: dict[str, str],
    record_id_shortener: Any = None,  # noqa: ANN401
    not_indexed: dict[str, dict[str, str]] | None = None,
) -> str:
    """A per-id explanation, or an empty string when every id resolved.

    A record that exists but has no indexed content gets its own block (its
    metadata and status) when the resolver described it: told only "not
    available", a model concludes the record does not exist.
    """
    if not not_available:
        return ""

    from app.utils.not_indexed import not_indexed_note

    not_indexed = not_indexed or {}
    grouped: dict[str, list[str]] = {}
    described: list[str] = []
    for record_id in not_available:
        reason = reasons.get(record_id, "not_available")
        if reason == "not_indexed_yet" and record_id in not_indexed:
            entry = not_indexed[record_id]
            block = entry.get("context") or f"Record ID: {record_id}"
            if record_id_shortener is not None:
                block = record_id_shortener.shorten_record_ids_in_text(block)
            described.append(f"{block}\n{not_indexed_note(entry.get('status'))}")
            continue
        # `shorten_if_known` never mints a label for an id the model has not
        # already seen -- an invented "R7" would be worse than the raw id.
        label = (
            record_id_shortener.shorten_if_known(record_id)
            if record_id_shortener is not None else record_id
        )
        grouped.setdefault(reason, []).append(label)

    lines = []
    for reason, ids in grouped.items():
        ids_str = ", ".join(f"'{rid}'" for rid in ids)
        lines.append(f"{ids_str}: {_UNAVAILABLE_PHRASING.get(reason, 'not available')}")
    note = "\n\nNote: " + "; ".join(lines) if lines else ""
    return note + "".join(f"\n\n{block}" for block in described)


def _not_read_note(
    record: dict[str, Any], record_id_shortener: "RecordIdShortener | None" = None,
) -> str:
    """What a record left without room in a multi-record call says instead of
    an empty shell the model would read as "not in the document"."""
    record_id = str(record.get("id") or record.get("virtual_record_id") or "")
    label = (
        record_id_shortener.shorten_if_known(record_id)
        if record_id_shortener is not None else record_id
    )
    return (
        f"\n[Record {label} was not read: this call named more records than one "
        f"result can hold. Call knowledgegraph__fetch_record with "
        f"record_ids=[\"{label}\"] to read it.]\n"
    )


async def _ids_outside_scope(context: "AgentContext", record_ids: list[str]) -> list[str]:
    return await ids_outside_scope(
        context.tool_state or {}, context.graph_provider, context.user_id, context.org_id, record_ids,
    )


# The longest query a focused-read note repeats back; a model's query is a
# phrase, and the note must not grow with a pasted paragraph.
_FOCUS_ECHO_CHARS = 200


def _focused_read_note(query: str) -> str:
    """What a focused read says after its records: it read a part, not the whole."""
    echo = " ".join(query.split())
    if len(echo) > _FOCUS_ECHO_CHARS:
        echo = echo[: _FOCUS_ECHO_CHARS - 1] + "…"
    return (
        f'\n[Focused read for "{echo}": a record too long to read whole shows its '
        "blocks most relevant to that query, with the gaps marked. For another part, "
        "call again with a different query; for the whole record, call without one.]\n"
    )


async def execute_fetch_record(
    *,
    context: "AgentContext",
    virtual_records: dict[str, Any],
    citation_ref_mapper: Any,  # noqa: ANN401
    record_ids: list[str] | str,
    reason: str = DEFAULT_FETCH_REASON,
    start_block: int = 0,
    max_blocks: int | None = None,
    start_block_sent: bool = False,
    query: str | None = None,
) -> tuple["ToolOutput", Any]:
    """Fetch one or more records end-to-end.

    Returns ``(output, ref_mapper)``. The mapper comes back rather than being
    stashed here, and is the SAME object passed in unless a record was actually
    rendered -- that identity is how the caller tells an update from a no-op.

    ``start_block_sent`` says the model named ``start_block`` itself, which is
    how an explicit 0 after a relevance selection is told from the default.

    ``query`` (``TARGETED_RETRIEVAL`` only) asks for part of each record: its
    blocks most relevant to the query, within ``FOCUSED_READ_MAX_CHARS`` per
    record, rather than the whole record. A record that fits that size is read
    whole; a continuation (``start_block`` > 0) reads in order from there,
    within the same size. Ignored under THOROUGH_RETRIEVAL's full-read policy.
    """
    from app.agent_loop_lib.tools.base import ToolOutput
    from app.agents.actions.knowledge_graph.ops.id_recovery import unresolved_id_hint
    from app.agents.agent_loop.answer_rules import READ_RECORDS_IN_FULL_STATE_KEY
    from app.agents.agent_loop.tool_adapter import _to_tool_output
    from app.modules.agents.qna.chat_state import remember_record_ids
    from app.modules.retrieval.context.targeted import (
        focused_read_chars,
        targeted_retrieval_enabled,
    )
    from app.utils.chat_helpers import (
        ImageBudget,
        _renderable_block_indices,
        image_dict_to_part,
        record_image_uris,
        record_to_message_content,
    )
    from app.utils.fetch_full_record import UNAVAILABLE, create_fetch_full_record_tool
    from app.utils.image_admission import ImageOrigin, admission_from_state
    from app.utils.record_block_selection import (
        build_selection_query,
        estimate_frame_chars,
        estimate_render_chars,
        select_relevant_blocks,
    )
    from app.utils.render_budget import (
        FETCH_SAFETY_MAX_BLOCKS,
        RecordRenderOutcome,
        fair_shares,
        fetch_size_by_chars_enabled,
        resolve_render_budget,
    )

    if isinstance(record_ids, str):
        record_ids = [record_ids]

    # TEMPORARY token-savings experiment (opt-in, disabled by default — see
    # `ChatQuery.enableRecordIdShortening`): resolve any short "R<n>" labels
    # (assigned by whichever knowledge tool ran first — see
    # `RecordIdShortener` in `utils/chat_helpers.py`) back to full Record
    # IDs before matching against `virtual_records`. IDs the model got
    # elsewhere are never shortened and pass through unchanged. Created
    # here (not just read) so a fetch that happens to be the first
    # knowledge call this request still shortens the ids it prints below.
    # `None` when the flag is off — record_ids pass through untouched.
    from app.utils.chat_helpers import get_record_id_shortener_if_enabled
    record_id_shortener = get_record_id_shortener_if_enabled(context.tool_state)
    if record_id_shortener is not None:
        record_ids = [record_id_shortener.resolve(rid) for rid in record_ids]

    # Intra-call only. Do NOT extend this to skip records fetched by an earlier
    # call: `shape_tool_result_clearing` drops stale fetch results and tells the
    # model to re-call with the same arguments, which such a guard would refuse.
    # After resolve so a short label and its full id collapse to one entry.
    record_ids = list(dict.fromkeys(record_ids))
    requested_max = _requested_max_blocks(max_blocks)
    by_chars = fetch_size_by_chars_enabled()
    if by_chars:
        # Characters decide how much is read. Some models send `max_blocks`
        # on every call by habit, so it is honoured only where it is a real
        # request: continuing a record from the offset it was handed.
        honoured = requested_max if start_block > 0 else None
        block_cap = min(FETCH_SAFETY_MAX_BLOCKS, honoured or FETCH_SAFETY_MAX_BLOCKS)
    else:
        block_cap = resolve_block_cap(requested_max)
    # One allowance for the whole call, split fairly between its records: N
    # records must not each get a full window's worth. Sized from the model
    # actually answering.
    budget = resolve_render_budget(context.context_length, max_blocks=block_cap)
    # Under THOROUGH_RETRIEVAL's full-read policy the records are read whole.
    focus = (
        (query or "").strip()
        if targeted_retrieval_enabled()
        and not context.tool_state.get(READ_RECORDS_IN_FULL_STATE_KEY)
        else ""
    )
    focused_limit = focused_read_chars() if focus else None
    if focused_limit is not None:
        # A focused read asks for a part: each record gets the focused size,
        # never more than the read it replaces.
        budget.max_chars = min(budget.max_chars, focused_limit * max(len(record_ids), 1))

    structured_tool = create_fetch_full_record_tool(
        virtual_records,
        org_id=context.org_id,
        graph_provider=context.graph_provider,
        user_id=context.user_id,
        connection_sources=connection_sources_of((context.tool_state or {}).get("filters")),
    )
    try:
        # A record outside what the turn is limited to is answered like one
        # that does not exist.
        outside = await _ids_outside_scope(context, record_ids)
        readable = [record_id for record_id in record_ids if record_id not in outside]
        result = (
            await structured_tool.coroutine(record_ids=readable, reason=reason)
            if readable else {"ok": False, "not_available_ids": []}
        )
        if outside and isinstance(result, dict):
            result["not_available_ids"] = [*(result.get("not_available_ids") or []), *outside]
    except Exception as exc:
        return ToolOutput(success=False, error=str(exc)), citation_ref_mapper

    def _recovery_hint() -> str:
        reasons = result.get("unavailable_reasons", {})
        unresolved = [
            rid for rid in result.get("not_available_ids", [])
            if reasons.get(rid, UNAVAILABLE) == UNAVAILABLE
        ]
        return unresolved_id_hint(
            unresolved,
            requested=record_ids,
            known_record_names=context.tool_state.get("known_record_names"),
            shortener=record_id_shortener,
        )

    if isinstance(result, dict) and result.get("ok") and result.get("records"):
        parts: list[str] = []
        ref_mapper = citation_ref_mapper
        records = result["records"]
        # Without `is_multimodal_llm` a record that IS an image (an uploaded
        # PNG whose only block is an IMAGE) renders to an empty string.
        # `collected_images` keeps the images out of the text join so they
        # can ride the multipart return below instead of being dropped.
        image_budget: ImageBudget = context.tool_state.setdefault("image_budget", ImageBudget())
        admission = admission_from_state(context.tool_state)
        collected_images: list[dict[str, Any]] = []
        # The model's query names the part it needs; the question would pull
        # the ranking back toward the passages search already showed.
        selection_query = focus or build_selection_query(context.query, reason)
        selected_before: set[str] = context.tool_state.setdefault(FETCH_SELECTED_RECORDS_KEY, set())
        # A summary, an overview, "what are the risks", "does it mention X
        # anywhere" — the answer is a property of the whole document, and
        # the parts relevance would drop are exactly the ones whose absence
        # changes the answer. Read it in order instead and let the model
        # continue; the router already classified the request
        # (`needs_whole_document`, set in `router.py`).
        # A focused read is the model asking for one part, whatever the question.
        wants_everything = not focus and bool(
            getattr(context, "needs_whole_document", False)
            or context.tool_state.get("needs_whole_document")
        )

        # Everything the result carries besides the records is held back
        # first, so the records cannot push it past the tool-result cap. The
        # notes are measured with full ids, which shortening only shrinks.
        entity_note = await _named_entity_note(context, records)
        budget.charge_framing(entity_note)
        focus_note = _focused_read_note(focus) if focus else ""
        budget.charge_framing(focus_note)
        budget.charge_framing_chars(
            len(_CITATION_RULE)
            + len(_unavailable_note(
                result.get("not_available_ids", []), result.get("unavailable_reasons", {}),
                None, result.get("not_indexed_records"),
            ))
            + len(_recovery_hint())
        )

        estimates = [estimate_render_chars(record, start_block) for record in records]
        selected_now = False
        unread: set[str] = set()
        for position, record in enumerate(records):
            record_key = str(record.get("id") or record.get("virtual_record_id") or "")
            budget.begin_record(record_key)
            # Fair shares for this record and the ones after it, re-planned
            # after every render so room a record did not use passes on. A
            # record may use everything not reserved for the later ones: the
            # estimate is rough, and a record that fits must not be cut short.
            remaining = budget.call_chars_remaining
            shares = fair_shares(estimates[position:], remaining)
            # The floor makes room for the records after this one; the last
            # record takes the rest of the call, however small, and is read
            # (a clipped prefix if need be) so that a retry advances.
            later = position < len(records) - 1
            if later and shares[0] < min(_MIN_RECORD_SHARE_CHARS, estimates[position]):
                unread.add(record_key)
                note = _not_read_note(record, record_id_shortener)
                budget.charge_framing(note)
                parts.append(note)
                continue
            limit = remaining - sum(shares[1:])
            if focused_limit is not None:
                # Room a smaller record left is not passed on: each record of a
                # focused read keeps to the focused size.
                limit = min(limit, focused_limit)
            budget.allot(record_key, limit)
            fits = estimates[position] <= limit
            # A continuation follows the pointer the model was handed, and an
            # explicit 0 on a record already shown by relevance is the model
            # asking for the in-order read the hint offered. Ranking either
            # again would return the blocks it already has.
            in_order = start_block > 0 or (start_block_sent and record_key in selected_before)
            # A record that does not fit loses something. Losing the tail is
            # the worst choice for the questions this tool answers, so the
            # blocks are ranked against what the model is looking for and the
            # gaps are announced.
            include_blocks = None
            if not (fits or wants_everything or in_order):
                include_blocks = await select_relevant_blocks(
                    record=record,
                    virtual_record_id=record.get("virtual_record_id"),
                    query=selection_query,
                    retrieval_service=context.retrieval_service,
                    user_id=context.user_id,
                    org_id=context.org_id,
                    budget=budget,
                    reserve=estimate_frame_chars(record, selection=True),
                )
            # The character allowance bounds size. A block count only limits
            # an in-order read of a record too large for its share, or what
            # the model asked for; it never cuts a record that fits or a
            # selection, which was sized by characters already.
            if by_chars:
                budget.allot(
                    record_key, limit, max_blocks=block_cap,
                    unbounded_blocks=include_blocks is not None,
                )
            else:
                budget.allot(
                    record_key, limit,
                    max_blocks=requested_max if fits else block_cap,
                    unbounded_blocks=include_blocks is not None or (fits and requested_max is None),
                )
            # The render is synchronous and this path is not: an image that
            # needs downscaling to the model's per-image limits is a Pillow
            # decode/resize/encode (~600 ms for a 4000x3000 page scan), and
            # inline that blocks every other request on the loop. Decide them
            # on a worker thread first so the render only reads the cache.
            if context.is_multimodal_llm:
                await admission.warm(record_image_uris(record))
            content_list, ref_mapper = record_to_message_content(
                record,
                ref_mapper=ref_mapper,
                start_block=start_block,
                is_multimodal_llm=context.is_multimodal_llm,
                collected_images=collected_images,
                image_budget=image_budget,
                image_admission=admission,
                image_origin=ImageOrigin.FETCHED_RECORD,
                budget=budget,
                include_blocks=include_blocks,
            )
            rendered = "".join(
                item["text"] for item in content_list if item.get("type") == "text"
            )
            outcome = budget.outcome(record_key)
            if include_blocks is not None and outcome.stop_reason == "selection":
                selected_before.add(record_key)
            if include_blocks is not None:
                selected_now = True
            if start_block > 0 and outcome.blocks_rendered == 0:
                indices = _renderable_block_indices(record)
                if not any(index >= start_block for index in indices):
                    last = max(indices) if indices else -1
                    note = (
                        f"\n[No blocks at offset {start_block}: this record's last "
                        f"renderable block is {last}.]\n"
                    )
                    budget.charge_framing(note)
                    rendered += note
            parts.append(rendered)

        text = "\n".join(parts)
        if focus and selected_now:
            text += focus_note
        text += entity_note
        # TEMPORARY token-savings experiment: shorten every "Record ID:"
        # this fetch prints back down to the same "R<n>" label the model
        # already saw — see `RecordIdShortener`. No-op (full ids as-is)
        # when the flag is off.
        if record_id_shortener is not None:
            text = record_id_shortener.shorten_record_ids_in_text(text)
        text += _CITATION_RULE
        text += _unavailable_note(
            result.get("not_available_ids", []),
            result.get("unavailable_reasons", {}),
            record_id_shortener,
            result.get("not_indexed_records"),
        )
        text += _recovery_hint()

        read_now = {
            str(record["id"]): record.get("record_name") or ""
            for record in result["records"] if record.get("id")
        }
        remember_record_ids(context.tool_state, read_now, names=read_now)
        for record in result["records"]:
            rid = record.get("id")
            if not rid:
                continue
            # Only a record read end to end counts as fetched: this set is what
            # hides records from candidate lists, and hiding one the model has
            # only partly seen is how a follow-up question loses its source.
            record_key = str(record.get("id") or record.get("virtual_record_id") or "")
            outcome = (
                RecordRenderOutcome(
                    record_id=record_key, stopped_at_block=start_block, stop_reason="not_read",
                )
                if record_key in unread else budget.outcome(record_key)
            )
            # A continuation that rendered nothing (an offset past the end)
            # read nothing, so it cannot finish a record.
            complete = outcome.complete and (outcome.blocks_rendered > 0 or start_block == 0)
            if complete:
                context.full_records_fetched.add(rid)
                context.tool_state.setdefault("full_records_fetched", set()).add(rid)
            if getattr(context, "include_retrieval_context", False):
                # The record lands in `virtual_record_id_to_result` whole, but
                # only the budgeted blocks reach the model; the ledger reports
                # that difference, which nothing else records.
                context.tool_state.setdefault("fetch_render_outcomes", {}).setdefault(
                    str(rid), [],
                ).append({
                    "virtualRecordId": record.get("virtual_record_id"),
                    "startBlock": start_block,
                    "blocksRendered": outcome.blocks_rendered,
                    "complete": complete,
                    "truncatedBy": (
                        None if complete
                        else outcome.stop_reason or ("past_end" if outcome.complete else "chars")
                    ),
                    "requestedMaxBlocks": requested_max,
                    "startBlockSent": start_block_sent,
                    "shownBlocks": sorted(outcome.shown_blocks),
                    "tableTruncation": (
                        {
                            "blockGroupIndex": outcome.table_truncation.group_index,
                            "rowsShown": outcome.table_truncation.rows_shown,
                            "rowsTotal": outcome.table_truncation.rows_total,
                        }
                        if outcome.table_truncation is not None else None
                    ),
                })

        if collected_images and context.is_multimodal_llm:
            # Mirrors `retrieval.py`'s matching branch: multipart `data`
            # flows through `ToolOutput` -> `ToolResult.content` ->
            # `ToolMessage.content` unchanged; a transport that cannot carry
            # images in a tool result relocates them itself.
            from app.agent_loop_lib.core.messages import TextPart

            image_parts = [
                part for img in collected_images
                if (part := image_dict_to_part(img)) is not None
            ]
            if image_parts:
                return ToolOutput(success=True, data=[TextPart(text=text), *image_parts]), ref_mapper

        return ToolOutput(success=True, data=text), ref_mapper

    if isinstance(result, dict) and not result.get("ok") and result.get("not_available_ids"):
        message = str(result.get("error") or "None of the requested records were available.")
        message += _unavailable_note(
            result["not_available_ids"], result.get("unavailable_reasons", {}), record_id_shortener,
            result.get("not_indexed_records"),
        )
        message += _recovery_hint()
        return ToolOutput(success=False, error=message), citation_ref_mapper

    return _to_tool_output(result), citation_ref_mapper
