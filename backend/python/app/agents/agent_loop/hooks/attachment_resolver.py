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
    PRE_MODEL hook that injects ``ImagePart`` objects into the initial
    ``UserMessage`` when the LLM supports vision.  Converts the
    ``image_url`` dicts stored on ``context.attachment_image_blocks`` into
    ``ImagePart`` instances on every model call — the stored context keeps
    the lightweight text version; the model always sees the image data.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import TurnContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext
    from app.utils.chat_helpers import ImageBudget

logger = logging.getLogger(__name__)

__all__ = [
    "attachment_rehydration",
    "resolve_attachments_for_goal",
    "resolve_history_attachments",
    "shape_image_injection",
    "shape_retrieved_image_injection",
]


# ---------------------------------------------------------------------------
# First-turn resolution (read-only — no blob writes)
# ---------------------------------------------------------------------------


async def resolve_attachments_for_goal(
    context: "AgentContext",
    log: logging.Logger,
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
    """
    attachments = context.tool_state.get("attachments") or []
    if not attachments:
        return "", []

    blob_store = context.blob_store
    if not blob_store:
        log.warning("No blob_store available; cannot resolve attachment content")
        return "", []

    from app.utils.attachment_utils import resolve_attachments  # noqa: PLC0415
    from app.utils.chat_helpers import CitationRefMapper  # noqa: PLC0415

    state = context.tool_state

    ref_mapper = state.get("citation_ref_mapper")
    if ref_mapper is None:
        ref_mapper = CitationRefMapper()
        state["citation_ref_mapper"] = ref_mapper

    from app.utils.chat_helpers import ImageBudget  # noqa: PLC0415
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
        b for b in blocks if b.get("type") == "image_url"
    ]

    text = ""
    if text_parts:
        text = "\n\nAttached files from the user:\n" + "\n".join(text_parts)

    # Append a compact table of attachable record IDs so the model can pass
    # them directly to attachment_record_ids in Slack/Outlook/Salesforce/Gmail
    # tool calls without requiring an intermediate lookup.
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
                "attachment_record_ids in Slack, Outlook, Gmail, and Salesforce "
                "tool calls to send the file as an attachment."
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
    from app.utils.chat_helpers import CitationRefMapper  # noqa: PLC0415
    from app.utils.chat_helpers import record_to_message_content  # noqa: PLC0415

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
        try:
            record = await blob_store.get_record_from_storage(
                virtual_record_id=vrid, org_id=context.org_id,
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
    from app.agents.agent_loop.hooks.citations import (  # noqa: PLC0415
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

    Returns ``(text, image_blocks)`` where *text* is the rendered
    ``<record>`` content (empty string if nothing resolved) and
    *image_blocks* is a list of LangChain ``image_url`` dicts for
    multimodal injection.
    """
    from app.utils.chat_helpers import ImageBudget, record_to_message_content  # noqa: PLC0415

    if image_budget is None:
        image_budget = ImageBudget()

    text_parts: list[str] = []
    image_blocks: list[dict[str, Any]] = []

    for att in attachments:
        if not isinstance(att, dict):
            continue
        mime = (att.get("mimeType") or "").lower()
        vrid = att.get("virtualRecordId") or ""
        if not vrid:
            continue

        if mime in _DOC_MIME_TYPES:
            try:
                if vrid in vrmap:
                    record = vrmap[vrid]
                else:
                    record = await blob_store.get_record_from_storage(
                        virtual_record_id=vrid, org_id=org_id,
                    )
                    if not record:
                        continue
                    vrmap[vrid] = record
                content_list, ref_mapper = record_to_message_content(
                    record, ref_mapper=ref_mapper, is_multimodal_llm=False,
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
                record = await blob_store.get_record_from_storage(
                    virtual_record_id=vrid, org_id=org_id,
                )
                if not record:
                    continue
                if vrid not in vrmap:
                    vrmap[vrid] = record
                blocks = _extract_image_urls_from_record(record, image_budget)
                image_blocks.extend(blocks)
            except Exception:
                logger.warning(
                    "Failed to resolve historical image attachment vrid=%s",
                    vrid, exc_info=True,
                )

    return "\n".join(text_parts), image_blocks


def _extract_image_urls_from_record(
    record: dict[str, Any], image_budget: "ImageBudget | None" = None,
) -> list[dict[str, Any]]:
    """Extract ``image_url`` blocks from a blob record's block_containers,
    respecting the shared conversation-wide ``image_budget`` (defaults to a
    fresh, effectively-unbounded one when not supplied)."""
    from app.utils.chat_helpers import ImageBudget, is_base64_image  # noqa: PLC0415

    if image_budget is None:
        image_budget = ImageBudget()

    block_containers = record.get("block_containers", {})
    blocks = (
        block_containers.get("blocks", [])
        if isinstance(block_containers, dict) else []
    )
    result: list[dict[str, Any]] = []
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
        if uri and is_base64_image(uri) and image_budget.can_add():
            image_budget.try_consume(1)
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
    from app.utils.chat_helpers import image_dict_to_part  # noqa: PLC0415

    return image_dict_to_part(block)


def shape_image_injection(context: "AgentContext") -> "Middleware[Any]":
    """PRE_MODEL hook: injects image attachment content into the initial
    ``UserMessage`` so the LLM receives actual image data instead of a
    text placeholder.

    Converts LangChain ``image_url`` dicts (stored on
    ``context.attachment_image_blocks`` by the factory after this hook is
    registered) into ``ImagePart`` objects and appends them to the first
    ``UserMessage``'s content on every model call.  The stored context
    keeps the lightweight text version; the model always sees the image.

    The check is deferred to dispatch time (not registration time) because
    ``_build_hooks`` runs before ``resolve_attachments_for_goal`` populates
    ``context.attachment_image_blocks``.
    """

    _cached_parts: list[Any] = []

    async def _middleware(ctx: Any, next_fn: "Next") -> None:
        if not _cached_parts and context.attachment_image_blocks:
            parts = [_langchain_image_to_part(b) for b in context.attachment_image_blocks]
            _cached_parts.extend(p for p in parts if p is not None)

        if not _cached_parts:
            await next_fn()
            return

        from app.agent_loop_lib.core.messages import (  # noqa: PLC0415
            TextPart,
            UserMessage,
        )

        _inject_into_first_user_message(ctx.messages, _cached_parts, TextPart, UserMessage)
        await next_fn()

    return _middleware


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


def _inject_into_first_user_message(
    messages: list[Any], parts: list[Any], text_part_cls: Any, user_message_cls: Any,
) -> None:
    """Append `parts` (`ImagePart`s) to the first `UserMessage` in
    `messages`, converting plain-string content to multipart first. Shared
    by `shape_image_injection` (attachment images) and
    `shape_retrieved_image_injection` (tool-sourced images, Ollama
    fallback) — same target message, same multipart-conversion rule.
    """
    for msg in messages:
        if not isinstance(msg, user_message_cls):
            continue
        content = msg.content
        if isinstance(content, str):
            msg.content = [text_part_cls(text=content), *parts]
        elif isinstance(content, list):
            # Dedup per image, not by "any image present" — the latter
            # would drop a whole batch of newly-popped
            # `pending_tool_images` (each batch is popped exactly once,
            # see `shape_retrieved_image_injection`, so a dropped batch is
            # gone for good) whenever the message already carried an
            # unrelated image, e.g. an attachment injected by
            # `shape_image_injection` or an earlier retrieved-image batch.
            #
            # Key on the normalised data URL rather than `source.data`: the
            # same image can arrive as a base64 source or a url source
            # depending on which producer built the part, and those two
            # carry different `data` strings for identical bytes.
            existing_sources = {
                _image_identity(p) for p in content
                if getattr(p, "type", None) == "image"
            }
            new_parts = [p for p in parts if _image_identity(p) not in existing_sources]
            if new_parts:
                msg.content = [*content, *new_parts]
        break


def shape_retrieved_image_injection(context: "AgentContext") -> "Middleware[Any]":
    """PRE_MODEL fallback hook: delivers tool-sourced images (search/fetch
    results) to providers whose LangChain integration strips images out of
    tool-result messages before they reach the provider (Ollama — see
    `LangChainTransport._supports_multipart_tool_result` /
    `converters.py`'s `strip_tool_images`).

    Only registered by `factory.py` when the resolved chat model needs it,
    so OpenAI/Anthropic (which deliver images natively via multipart
    `ToolMessage` content) never see the same image injected twice.

    Pops (not peeks) `context.tool_state["pending_tool_images"]` on every
    dispatch — `retrieval.py`/`citations.py` repopulate it fresh on each
    tool call within the turn, so each batch of tool-sourced images is
    delivered exactly once, on the very next model call after the tool
    call that produced them.
    """

    async def _middleware(ctx: Any, next_fn: "Next") -> None:
        pending = context.tool_state.pop("pending_tool_images", [])
        if not pending:
            await next_fn()
            return

        from app.utils.chat_helpers import image_dict_to_part  # noqa: PLC0415

        image_parts = [p for img in pending if (p := image_dict_to_part(img)) is not None]
        if not image_parts:
            await next_fn()
            return

        from app.agent_loop_lib.core.messages import (  # noqa: PLC0415
            TextPart,
            UserMessage,
        )

        _inject_into_first_user_message(ctx.messages, image_parts, TextPart, UserMessage)
        await next_fn()

    return _middleware
