"""`attachment_resolver`: attachment handling for the agent loop.

Three public entry points:

``resolve_attachments_for_goal``
    Async helper called from ``factory.create()`` **before** ``agent.run()``.
    Resolves the current turn's uploaded attachments from blob storage (where
    they were already persisted by the upload API), populates citation maps
    (``virtual_record_id_to_result``, ``citation_ref_mapper``), and returns
    ``(text_for_goal, image_blocks)`` — the rendered ``<record>`` text for
    ``goal.description`` and any ``image_url`` dicts for multimodal injection.

    Read-only — no blob writes.  The parsed record is already in blob storage
    via the upload API (``POST /api/v1/chat/attachments/upload``), keyed by
    ``virtualRecordId``.  Cross-turn access is handled by
    ``attachment_rehydration`` which re-fetches from the SAME blob record.

``attachment_rehydration``
    PRE_TURN hook (turn 0 only) for **follow-up** HTTP requests.  Re-fetches
    historical attachment records from blob by ``virtualRecordId`` so
    ``virtual_record_id_to_result`` and ``citation_ref_mapper`` are populated
    again — enabling ``dynamic_fetch_full_record`` and citation resolution
    without the model having to re-upload the file.

``shape_image_injection``
    PRE_MODEL hook that puts ``ImagePart`` objects on the current request
    when the LLM supports vision.  Converts the ``image_url`` dicts stored on
    ``context.attachment_image_blocks`` into ``ImagePart`` instances on every
    model call, on a copy of the message — the stored context keeps the
    lightweight text version; the model always sees the image data.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import TurnContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext
    from app.utils.chat_helpers import ImageBudget
    from app.utils.image_admission import ImageAdmission
    from app.utils.render_budget import RecordRenderOutcome, RenderBudget

logger = logging.getLogger(__name__)

__all__ = [
    "attachment_rehydration",
    "resolve_attachments_for_goal",
    "resolve_history_attachments",
    "shape_image_injection",
]


# ---------------------------------------------------------------------------
# First-turn resolution (read-only — no blob writes)
# ---------------------------------------------------------------------------

# Marks an `image_url` dict on `context.attachment_image_blocks` as the user's
# own attachment. Prefetch adds retrieved images to the same list, and the
# transport's image cap keeps the user's before those.
USER_ATTACHED_KEY = "user_attached"


def default_message_budget(context: "AgentContext") -> int:
    """The message budget for `context`'s model when the system prompt is not
    known yet. The factory passes the real one; this serves direct callers."""
    from app.agent_loop_lib.context.base import ContextBudget

    return ContextBudget.for_model(
        context.model_name or None, context_window=context.configured_context_window,
    ).effective_max_tokens




async def resolve_attachments_for_goal(
    context: "AgentContext",
    log: logging.Logger,
    *,
    max_chars: int | None = None,
    outcomes: "list[RecordRenderOutcome] | None" = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Resolve current-turn attachments from blob and populate citation maps.

    Called from ``PipesHubAgentFactory.create()`` before ``agent.run()``.
    The parsed records are already in blob storage (uploaded via
    ``POST /api/v1/chat/attachments/upload``); this function only READS
    them and builds the in-memory citation state the agent needs.

    Returns ``(text_for_goal, image_blocks)`` where *text_for_goal* is the
    combined ``<record>`` text for appending to ``goal.description`` (empty
    string when nothing was resolved) and *image_blocks* is a list of
    LangChain ``{"type": "image_url", ...}`` dicts to be injected into the
    initial ``UserMessage`` when the LLM supports vision.

    Documents render within *max_chars* (by default the current attachments'
    share of the model's message budget): a document is inlined into the
    current request, which no compaction layer shortens, so an unbounded one
    alone could overflow the window. One cut short says where it stopped and
    how to read on. Images go through the request's `ImageAdmission` as the
    user's own (`ImageOrigin.ATTACHMENT`), so the per-model cap, dedup and
    downscale apply to them as to every other image.
    """
    attachments = context.tool_state.get("attachments") or []
    if not attachments:
        return "", []

    blob_store = context.blob_store
    if not blob_store:
        log.warning("No blob_store available; cannot resolve attachment content")
        return "", []

    from app.agents.agent_loop.history_window import current_attachment_chars
    from app.utils.attachment_utils import resolve_attachments
    from app.utils.chat_helpers import CitationRefMapper
    from app.utils.image_admission import admission_from_state
    from app.utils.render_budget import RenderBudget

    state = context.tool_state
    if max_chars is None:
        max_chars = current_attachment_chars(default_message_budget(context))

    ref_mapper = state.get("citation_ref_mapper")
    if ref_mapper is None:
        ref_mapper = CitationRefMapper()
        state["citation_ref_mapper"] = ref_mapper

    from app.utils.chat_helpers import ImageBudget
    image_budget: ImageBudget = state.setdefault("image_budget", ImageBudget())

    attachment_records: dict[str, dict[str, Any]] = {}
    try:
        blocks = await resolve_attachments(
            attachments=attachments,
            blob_store=blob_store,
            org_id=context.org_id,
            is_multimodal_llm=context.is_multimodal_llm,
            logger=log,
            ref_mapper=ref_mapper,
            out_records=attachment_records,
            image_budget=image_budget,
            image_admission=admission_from_state(state),
            user_id=context.user_id,
            graph_provider=context.graph_provider,
            is_service_account=context.is_service_account,
            render_budget=RenderBudget(max_chars=max(max_chars, 0)),
            outcomes=outcomes,
        )
    except Exception as exc:
        log.warning("Failed to resolve attachments: %s", exc)
        state["resolved_attachment_blocks"] = []
        return "", []

    _merge_records_into_state(state, attachment_records)
    state["resolved_attachment_blocks"] = blocks

    if not blocks:
        return "", []

    text_parts = [
        b["text"] for b in blocks if b.get("type") == "text" and b.get("text")
    ]
    image_blocks = [
        {**b, USER_ATTACHED_KEY: True} for b in blocks if b.get("type") == "image_url"
    ]

    text = ""
    if text_parts:
        text = "\n\nAttached files from the user:\n" + "\n".join(text_parts)

    # Append a compact table of attachable record IDs so the model can pass
    # them directly to any tool's attachment_record_ids without an intermediate lookup.
    attachable_rows = _build_attachable_records_table(attachments, attachment_records)
    if attachable_rows:
        text = (text or "") + (
            "\n\nAttachable record IDs (pass to attachment_record_ids in tool calls):\n"
            + attachable_rows
        )

    return text, image_blocks


def _build_attachable_records_table(
    attachments: list[dict[str, Any]],
    attachment_records: dict[str, dict[str, Any]],
) -> str:
    """Return a compact markdown list of filename → recordId pairs.

    Used to inject attachable record IDs into the goal description so the
    model can pass them to attachment_record_ids without extra tool calls.
    """
    rows: list[str] = []
    for att in attachments:
        vrid = att.get("virtualRecordId") or ""
        name = att.get("recordName") or att.get("name") or "file"
        rec = attachment_records.get(vrid) or {}
        rec_id = rec.get("id") or rec.get("_key") or ""
        if rec_id:
            rows.append(f"- {name}: `{rec_id}`")
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Follow-up turn rehydration (PRE_TURN hook)
# ---------------------------------------------------------------------------


def attachment_rehydration(context: "AgentContext") -> "Middleware[TurnContext]":
    """PRE_TURN hook: re-populates citation maps from historical attachments.

    On follow-up HTTP requests the process-local ``virtual_record_id_to_result``
    and ``citation_ref_mapper`` are cold.  This hook scans
    ``previousConversations`` for ``user_query`` turns that carried
    attachments, re-fetches the underlying records from blob storage, and
    populates both maps so ``dynamic_fetch_full_record`` and citation
    resolution work exactly as they did on the original turn.

    Also appends a ``goal.constraints`` reminder so the model knows which
    files were previously attached and how to access their content.
    """

    async def _middleware(ctx: "TurnContext", next_fn: "Next") -> None:
        if ctx.turn_index != 0 or ctx.scope is None:
            await next_fn()
            return

        previous = context.previous_conversations or []
        if not previous:
            await next_fn()
            return

        historical = _collect_historical_attachments(previous)
        if not historical:
            await next_fn()
            return

        blob_store = context.blob_store
        if not blob_store:
            await next_fn()
            return

        record_ids = await _rehydrate_citation_maps(
            context, historical, blob_store,
        )

        if record_ids:
            _register_fetch_tool(ctx, context)

        goal = ctx.scope.run.goal
        names = [a.get("recordName", "unknown") for a in historical]

        if record_ids:
            ids_str = ", ".join(f'"{rid}"' for rid in record_ids)
            goal.constraints.append(
                f"User previously attached file(s): {', '.join(names)}. "
                f"Call knowledgegraph__fetch_record(record_ids=[{ids_str}]) "
                "to retrieve their full content. "
                f"These same record IDs ([{ids_str}]) can also be passed as "
                "attachment_record_ids in any tool that has that parameter (for example Slack, "
                "Outlook, Gmail, Salesforce, Jira, and Confluence) to send the file as an attachment."
            )
        else:
            goal.constraints.append(
                f"User previously attached file(s): {', '.join(names)}. "
                "The content could not be loaded for this turn."
            )

        await next_fn()

    return _middleware


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _collect_historical_attachments(
    previous_conversations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract attachment refs from ``user_query`` turns in history."""
    result: list[dict[str, Any]] = []
    for turn in previous_conversations:
        if turn.get("role") != "user_query":
            continue
        for att in turn.get("attachments") or []:
            if att.get("virtualRecordId"):
                result.append(att)
    return result


async def _rehydrate_citation_maps(
    context: "AgentContext",
    attachments: list[dict[str, Any]],
    blob_store: Any,
) -> list[str]:
    """Re-fetch records from blob and populate citation state.

    Returns actual record IDs (``record["id"]``) that were resolved —
    these are the IDs the model should pass to ``dynamic_fetch_full_record``.
    """
    from app.modules.artifacts.reader import record_artifact_reader
    from app.utils.chat_helpers import CitationRefMapper, record_to_message_content
    from app.utils.record_access import caller_can_read_virtual_record

    state = context.tool_state

    ref_mapper = state.get("citation_ref_mapper")
    if ref_mapper is None:
        ref_mapper = CitationRefMapper()
        state["citation_ref_mapper"] = ref_mapper

    vrmap: dict[str, Any] = state.get("virtual_record_id_to_result") or {}
    if not isinstance(vrmap, dict):
        vrmap = {}
    state["virtual_record_id_to_result"] = vrmap

    resolved_ids: list[str] = []

    for att in attachments:
        vrid = att["virtualRecordId"]
        if vrid in vrmap:
            rec_id = vrmap[vrid].get("id")
            if rec_id:
                resolved_ids.append(rec_id)
            continue
        if not await caller_can_read_virtual_record(
            context.graph_provider,
            user_id=context.user_id,
            org_id=context.org_id,
            virtual_record_id=vrid,
            logger=logger,
            is_service_account=context.is_service_account,
        ):
            continue
        try:
            record = await record_artifact_reader(blob_store).read_record(
                vrid, context.org_id,
            )
            if record:
                vrmap[vrid] = record
                record_to_message_content(
                    record, ref_mapper=ref_mapper, is_multimodal_llm=False,
                )
                rec_id = record.get("id")
                if rec_id:
                    resolved_ids.append(rec_id)
        except Exception as exc:
            logger.warning(
                "Failed to rehydrate attachment %s (vrid=%s): %s",
                att.get("recordName", "unknown"), vrid, exc,
            )

    return resolved_ids


def _register_fetch_tool(
    ctx: "TurnContext",
    context: "AgentContext",
) -> None:
    """Early-register ``dynamic_fetch_full_record`` via ``citations.py``.

    Delegates to ``ensure_fetch_full_record_available`` which owns all
    registration + grant logic for this tool.

    Also patches ``run_scope.visible_tools`` when it was already computed
    by the PRE_AGENT ``tool_preloading`` hook — without this, a tool
    registered after that hook's ``initial_visible_tools()`` snapshot stays
    invisible to ``tool_schemas_for_turn`` for the entire run.
    """
    from app.agents.agent_loop.hooks.citations import (
        _FETCH_FULL_RECORD_TOOL_NAME,
        ensure_fetch_full_record_available,
    )

    run_scope = ctx.scope.run if ctx.scope is not None else None
    registry = run_scope.runtime.tool_registry if run_scope is not None else None
    if registry is None:
        return

    ensure_fetch_full_record_available(
        context, run_scope=run_scope, registry=registry,
    )

    if run_scope is not None and getattr(run_scope, "visible_tools", None) is not None:
        run_scope.visible_tools.add(_FETCH_FULL_RECORD_TOOL_NAME)


def _merge_records_into_state(
    state: dict[str, Any],
    attachment_records: dict[str, dict[str, Any]],
) -> None:
    """Merge resolved records into ``virtual_record_id_to_result``."""
    if not attachment_records:
        return
    vrmap = state.get("virtual_record_id_to_result")
    if not isinstance(vrmap, dict):
        vrmap = {}
        state["virtual_record_id_to_result"] = vrmap
    for vrid, rec in attachment_records.items():
        if vrid not in vrmap:
            vrmap[vrid] = rec


# ---------------------------------------------------------------------------
# History-seeding: resolve attachments (documents + images) for a single turn
# ---------------------------------------------------------------------------

_DOC_MIME_TYPES = frozenset({"application/pdf", "text/plain", "text/markdown"})
# Below this a document's render would be little more than its header; it is
# named instead, with how to read it.
_MIN_HISTORY_ATTACHMENT_CHARS = 2_000
_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg"})


async def resolve_history_attachments(
    attachments: list[dict[str, Any]],
    blob_store: Any,
    org_id: str,
    ref_mapper: Any,
    vrmap: dict[str, Any],
    *,
    is_multimodal_llm: bool = False,
    image_budget: "ImageBudget | None" = None,
    image_admission: "ImageAdmission | None" = None,
    user_id: str | None = None,
    graph_provider: Any = None,
    is_service_account: bool = False,
    render_budget: "RenderBudget | None" = None,
    outcomes: "list[RecordRenderOutcome] | None" = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Resolve document and image attachments from blob for a historical turn.

    Called from ``_seed_conversation_history`` (factory) to inject
    attachment content directly into the conversation messages — matching
    the old agent's ``_build_conversation_messages`` behavior where the
    model always has file content in context.

    Populates *vrmap* (``virtual_record_id_to_result``) and *ref_mapper*
    as side-effects so ``dynamic_fetch_full_record`` and citation
    resolution remain functional after auto-compaction summarizes this
    text away.

    ``image_budget`` should be the SAME instance current-turn attachments
    and every search/fetch/prefetch tool call debit
    (``context.tool_state["image_budget"]``) — historical images count
    against the same 50-image conversation cap, not a separate one.
    Defaults to a fresh (effectively unbounded for this call alone)
    budget when not supplied.

    ``image_admission`` is the request's arbiter. Replayed images are
    ``ImageOrigin.HISTORY`` -- the lowest tier -- and this runs during history
    seeding, before any of the turn's own tool calls: on the raw budget alone,
    old images spent the allowance ahead of the record the user is actually
    asking about, and skipped dedup and the downscale to the provider's
    per-image limits on the way.

    ``render_budget`` is what earlier turns' documents may render into,
    shared across every turn the seeding replays (the history window's
    attachment share). A document cut short ends with a note saying where it
    stopped and how to read on; one with no room left is named, not inlined.
    None renders documents in full.

    Returns ``(text, image_blocks)`` where *text* is the rendered
    ``<record>`` content (empty string if nothing resolved) and
    *image_blocks* is a list of LangChain ``image_url`` dicts for
    multimodal injection.
    """
    from app.agents.agent_loop.history_window import attachment_stub
    from app.modules.artifacts.reader import record_artifact_reader
    from app.utils.attachment_utils import render_attachment_record
    from app.utils.chat_helpers import ImageBudget
    from app.utils.image_admission import ImageOrigin
    from app.utils.record_access import caller_can_read_virtual_record

    if image_budget is None:
        image_budget = ImageBudget()

    text_parts: list[str] = []
    image_blocks: list[dict[str, Any]] = []
    not_inlined: list[str] = []
    docs_left = sum(
        1 for att in attachments
        if isinstance(att, dict) and att.get("virtualRecordId")
        and (att.get("mimeType") or "").lower() in _DOC_MIME_TYPES
    )

    for att in attachments:
        if not isinstance(att, dict):
            continue
        mime = (att.get("mimeType") or "").lower()
        vrid = att.get("virtualRecordId") or ""
        if not vrid:
            continue
        if mime not in _DOC_MIME_TYPES and not (
            mime in _IMAGE_MIME_TYPES and is_multimodal_llm
        ):
            continue
        if not await caller_can_read_virtual_record(
            graph_provider,
            user_id=user_id,
            org_id=org_id,
            virtual_record_id=vrid,
            logger=logger,
            is_service_account=is_service_account,
        ):
            continue

        if mime in _DOC_MIME_TYPES:
            sharing = docs_left
            docs_left -= 1
            if render_budget is not None and render_budget.call_chars_remaining < _MIN_HISTORY_ATTACHMENT_CHARS:
                not_inlined.append(str(att.get("recordName") or "a file"))
                continue
            try:
                if vrid in vrmap:
                    record = vrmap[vrid]
                else:
                    record = await record_artifact_reader(blob_store).read_record(
                        vrid, org_id,
                    )
                    if not record:
                        continue
                    vrmap[vrid] = record
                content_list, ref_mapper = render_attachment_record(
                    record, ref_mapper=ref_mapper, is_multimodal_llm=False,
                    image_budget=None, image_admission=None,
                    image_origin=ImageOrigin.HISTORY,
                    render_budget=render_budget, docs_left=sharing,
                    outcomes=outcomes, logger=logger,
                )
                text = "".join(
                    item["text"] for item in content_list
                    if item.get("type") == "text"
                )
                if text:
                    text_parts.append(text)
            except Exception:
                logger.warning(
                    "Failed to resolve historical doc attachment vrid=%s",
                    vrid, exc_info=True,
                )

        elif mime in _IMAGE_MIME_TYPES and is_multimodal_llm:
            try:
                record = await record_artifact_reader(blob_store).read_record(
                    vrid, org_id,
                )
                if not record:
                    continue
                if vrid not in vrmap:
                    vrmap[vrid] = record
                blocks = _extract_image_urls_from_record(
                    record, image_budget, image_admission=image_admission,
                )
                image_blocks.extend(blocks)
            except Exception:
                logger.warning(
                    "Failed to resolve historical image attachment vrid=%s",
                    vrid, exc_info=True,
                )

    if not_inlined:
        text_parts.append(attachment_stub(not_inlined))
    return "\n".join(text_parts), image_blocks


def _extract_image_urls_from_record(
    record: dict[str, Any],
    image_budget: "ImageBudget | None" = None,
    *,
    image_admission: "ImageAdmission | None" = None,
) -> list[dict[str, Any]]:
    """Extract ``image_url`` blocks from a blob record's block_containers.

    Goes through the request's `ImageAdmission` so a replayed image obeys the
    same per-model cap, dedup and per-image downscale as every other source,
    and is ranked as `ImageOrigin.HISTORY` rather than taking a slot simply
    for being seeded first. Falls back to the shared conversation-wide
    ``image_budget`` alone when no arbiter is supplied.
    """
    from app.utils.chat_helpers import ImageBudget, admission_for, is_base64_image
    from app.utils.image_admission import ImageCandidate, ImageOrigin

    if image_budget is None:
        image_budget = ImageBudget()
    admission = admission_for(image_admission, image_budget)

    block_containers = record.get("block_containers", {})
    blocks = (
        block_containers.get("blocks", [])
        if isinstance(block_containers, dict) else []
    )
    candidates: list[ImageCandidate] = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "image":
            continue
        data = block.get("data")
        if isinstance(data, dict):
            uri = data.get("uri", "")
        elif isinstance(data, str):
            uri = data
        else:
            continue
        if not (uri and is_base64_image(uri)):
            continue
        candidates.append(ImageCandidate(
            ref="",
            data_uri=uri,
            origin=ImageOrigin.HISTORY,
            block_index=int(block.get("index") or 0),
        ))

    # One batch, not one call per image: ranking needs the whole set in hand.
    outcome = admission.admit(candidates)
    # `admitted_uri` answers "did this win a slot in the request", which is
    # yes for an image some earlier batch already put on the wire. Emitting on
    # that alone attaches a second copy of it here.
    #
    # Identity, not the URI: within one batch the copies this call rejected as
    # duplicates carry the SAME `data_uri` as the copy it admitted, so keying
    # on the string would withhold the winner along with them and the image
    # would vanish. `outcome.degraded` holds the very objects passed in, and
    # `candidates` outlives the loop, so `id()` is both exact and free of a
    # sha256 over a multi-megabyte payload.
    withheld = {id(degraded.candidate) for degraded in outcome.degraded}
    # Looked up per source URI rather than keyed by `block_index`. A record is
    # blob data and `Block.index` defaults to None, so several image blocks can
    # collapse to index 0 -- which made this emit one image twice and lose the
    # other. Not keyed off `admit()`'s returned list either: those candidates
    # carry the *normalized* bytes, so their hash never matches the source.
    result: list[dict[str, Any]] = []
    emitted: set[str] = set()
    for candidate in candidates:
        if id(candidate) in withheld:
            continue
        # The admitted bytes, downscaled if the model's per-image limits
        # required it; None when this image did not win a slot.
        uri = admission.admitted_uri(candidate.data_uri)
        if uri is None or uri in emitted:
            continue
        emitted.add(uri)
        result.append({"type": "image_url", "image_url": {"url": uri}})
    return result


# ---------------------------------------------------------------------------
# PRE_MODEL hook: multimodal image injection
# ---------------------------------------------------------------------------


def _langchain_image_to_part(block: dict[str, Any]) -> Any:
    """Convert a LangChain ``image_url`` dict to an ``ImagePart``. Thin
    alias over ``chat_helpers.image_dict_to_part`` (shared with
    ``retrieval.py``/``citations.py``'s tool-result image collection) kept
    under this name since it's the established public symbol other modules
    already import."""
    from app.utils.chat_helpers import image_dict_to_part

    return image_dict_to_part(block)


def shape_image_injection(context: "AgentContext") -> "Middleware[Any]":
    """PRE_MODEL hook: puts the current turn's image attachments on the
    current request so the LLM receives the image data, not a placeholder.

    Converts LangChain ``image_url`` dicts (stored on
    ``context.attachment_image_blocks`` by the factory after this hook is
    registered) into ``ImagePart`` objects on every model call. The images
    go on a copy of the current request (``context/protection.py``), never on
    the stored message: in a chat the first user message is an earlier
    question, and writing to a stored message would carry the images into
    every later call and turn.

    The check is deferred to dispatch time (not registration time) because
    ``_build_hooks`` runs before ``resolve_attachments_for_goal`` populates
    ``context.attachment_image_blocks``.

    Sub-agents share this hook (one kernel per request) but not the user's
    attachments: a child's request is its delegated task, so only the root
    run's calls carry them.
    """

    _cached_parts: list[Any] = []

    async def _middleware(ctx: Any, next_fn: "Next") -> None:
        if getattr(ctx, "is_child_run", False):
            await next_fn()
            return
        if not _cached_parts and context.attachment_image_blocks:
            _cached_parts.extend(_attachment_image_parts(context.attachment_image_blocks))

        if _cached_parts:
            ctx.messages = _with_images_on_current_request(ctx.messages, _cached_parts)
        await next_fn()

    return _middleware


def _attachment_image_parts(blocks: list[dict[str, Any]]) -> list[Any]:
    """`ImagePart`s for `blocks`, the user's own marked as attached."""
    parts = []
    for block in blocks:
        part = _langchain_image_to_part(block)
        if part is None:
            continue
        if block.get(USER_ATTACHED_KEY):
            part = part.model_copy(update={"user_attached": True})
        parts.append(part)
    return parts


def _image_identity(part: Any) -> str | None:
    """A producer-independent identity for an `ImagePart`, so a base64 source
    and a url source carrying the same image compare equal."""
    from app.agent_loop_lib.core.messages import image_data_url

    source = getattr(part, "source", None)
    if source is None:
        return None
    try:
        return image_data_url(source)
    except Exception:
        return getattr(source, "data", None)


def _with_images_on_current_request(messages: list[Any], parts: list[Any]) -> list[Any]:
    """A copy of `messages` whose current request carries `parts`
    (`ImagePart`s), converting plain-string content to multipart first.
    Images the request already carries are not added twice. The message
    objects in `messages` are never modified."""
    from app.agent_loop_lib.context.protection import current_request_index
    from app.agent_loop_lib.core.messages import TextPart

    index = current_request_index(messages)
    if index is None:
        return messages
    request = messages[index]
    content = request.content
    if isinstance(content, str):
        new_content = [TextPart(text=content), *parts]
    else:
        # Key on the normalised data URL rather than `source.data`: the same
        # image can arrive as a base64 source or a url source depending on
        # which producer built the part, and those two carry different
        # `data` strings for identical bytes.
        existing = {_image_identity(p) for p in content if getattr(p, "type", None) == "image"}
        new_parts = [p for p in parts if _image_identity(p) not in existing]
        if not new_parts:
            return messages
        new_content = [*content, *new_parts]
    shaped = list(messages)
    shaped[index] = request.model_copy(update={"content": new_content})
    return shaped
