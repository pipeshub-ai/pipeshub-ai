"""LLM-based query routing: classifies a user request into PipesHub's three
execution tiers (`quick` / `react` / `deep`).

Extracted from `app/api/routes/agent.py::_auto_select_graph` (Phase 7 of the
agent-loop migration) so the new agent-loop auto-router
(`app/agents/agent_loop/router.py`) and the legacy LangGraph route selector
share exactly one classification prompt/heuristic instead of two forks that
would silently drift apart. This module stops at the tier decision itself —
mapping a tier to a `CompiledStateGraph` (legacy) or a `LoopStrategy`
(agent-loop) is each caller's own concern.
"""

from __future__ import annotations

from logging import Logger
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from app.modules.agents.capability_summary import (
    classify_knowledge_sources,
    format_connector_filter_lines,
)
from app.modules.agents.qna.tool_system import code_execution_enabled
from app.modules.transformers.blob_storage import BlobStorage
from app.utils.attachment_utils import resolve_attachments


class RouteDecision(BaseModel):
    """Routing decision with structured chain-of-thought reasoning.

    reasoning: structured analysis — sub-tasks identified, dependency chain,
               parameter availability, and justification for the chosen tier.
               Written BEFORE committing to a route (CoT reduces misroutes).
    route: the tier — type-safe, cannot produce an invalid value.
    """

    reasoning: str
    route: Literal["quick", "react", "deep"]


def build_capability_context(
    query_info: dict[str, Any],
) -> tuple[str, int, list[dict], list[dict], list[dict]]:
    """
    Build a rich capability summary for the routing prompt.

    Prefers fully-labeled data when available (chat_stream path supplies
    query_info["knowledge"] and query_info["toolsets"]).  Falls back
    gracefully to filter counts + bare tool-name strings when only the
    lighter query_info structure is present (non-streaming chat / askAI).

    Returns:
        (capability_block, n_knowledge, indexed_connectors, kb_sources, tools_data)
        where tools_data is a list of {"full_name": str, "desc": str} dicts.
    """
    lines: list[str] = ["## Agent capabilities\n"]
    indexed_connectors: list[dict] = []
    kb_sources: list[dict] = []

    # ── Knowledge sources ─────────────────────────────────────────────────
    agent_knowledge: list[dict] = query_info.get("knowledge") or []
    connector_cfgs = query_info.get("connector_configs") or {}

    if agent_knowledge:
        kb_sources, indexed_connectors = classify_knowledge_sources(
            agent_knowledge,
            connector_configs=connector_cfgs if isinstance(connector_cfgs, dict) else None,
        )
        n_knowledge = len(kb_sources) + len(indexed_connectors)
        lines.append(f"Knowledge sources ({n_knowledge} total):")
        for c in indexed_connectors:
            line = f"  • {c['label']} — app connector (type: {c['type_key']})"
            fls = format_connector_filter_lines(c.get("filters"))
            if fls:
                line += "; " + "; ".join(fls)
            lines.append(line)
        for k in kb_sources:
            cids = k.get("collection_ids", [])
            scope = f", {len(cids)} scoped collection(s)" if cids else ""
            lines.append(f"  • {k['label']} — knowledge base{scope}")
    else:
        # Fallback: derive counts from filters (NO_KB_SELECTED sentinel excluded)
        filters = query_info.get("filters") or {}
        n_connectors = len(filters.get("apps") or [])
        n_kb = len([
            x for x in (filters.get("kb") or [])
            if x and x != "NO_KB_SELECTED"
        ])
        n_knowledge = n_connectors + n_kb
        if n_knowledge:
            lines.append(
                f"Knowledge sources ({n_knowledge} total): "
                f"{n_connectors} connector(s), {n_kb} KB collection(s)"
            )
        else:
            lines.append("Knowledge sources: none configured")

    lines.append("")

    # ── Action tools ─────────────────────────────────────────────────────
    # Prefer toolsets (rich: fullName + description per tool).
    # Fall back to the flat "tools" string list when toolsets are absent.
    tools_data: list[dict] = []  # {"full_name": str, "desc": str}

    toolsets: list[dict] = query_info.get("toolsets") or []
    if toolsets:
        for ts in toolsets:
            for tool in ts.get("tools", []):
                full_name = tool.get("fullName") or tool.get("name", "")
                if not full_name:
                    continue
                desc = (tool.get("description") or "").strip()
                tools_data.append({"full_name": full_name, "desc": desc})
    else:
        raw_tools: list = query_info.get("tools") or []
        for t in raw_tools:
            if isinstance(t, str) and t:
                tools_data.append({"full_name": t, "desc": ""})

    if tools_data:
        lines.append(f"Action tools ({len(tools_data)} total):")
        for td in tools_data:
            entry = f"  • {td['full_name']}"
            if td["desc"]:
                entry += f" — {td['desc'][:100]}"
            lines.append(entry)
    else:
        lines.append("Action tools: none configured")

    # Internal/built-in tools (calculator, code-execution sandbox, image
    # generation, ...) are always loaded by `tool_system.py` regardless of
    # `toolsets`/`tools` above — they are NOT reflected in `tools_data`,
    # which only covers agent-configured connector toolsets. Without this
    # line the classifier only ever sees connector tools and can reason
    # "no available tool can do X" for capabilities (file/chart/document
    # generation, math) that the agent actually has via internal tools —
    # a routing-prompt gap, not a real capability gap. Resolved with the
    # SAME precedence `code_execution_enabled()` uses elsewhere so this
    # stays in sync with the real per-deployment/per-request gate.
    lines.append("")
    if code_execution_enabled(query_info):
        lines.append(
            "Built-in tools (always available, independent of the action "
            "tools above): a code-execution sandbox that can generate "
            "files — charts, documents (DOCX/PPTX/PDF), spreadsheets, "
            "images, CSVs — plus a calculator."
        )
    else:
        lines.append(
            "Built-in tools (always available): a calculator. "
            "(Code-execution/file-generation tools are disabled on this deployment.)"
        )

    return "\n".join(lines) + "\n\n", n_knowledge, indexed_connectors, kb_sources, tools_data


async def build_prior_routing_messages(
    query_info: dict[str, Any],
    blob_store: Any = None,
    org_id: str = "",
    is_multimodal_llm: bool = False,
) -> list:
    """
    Build prior conversation turns as LangChain HumanMessage/AIMessage objects
    for the routing LLM call.

    Each user turn's content list is assembled in document order:
    - Turn text comes first.
    - PDF attachments: text blocks and embedded image blocks are interleaved
      exactly as they appear in the document (images only when multimodal).
    - Standalone image attachments: appended after the turn text (multimodal only).

    Bot turns are truncated to their first line to keep the context compact.
    Attachment fetching errors are silently swallowed so routing is never blocked.
    """
    from app.utils.attachment_utils import resolve_attachment_blocks_simple
    from app.utils.chat_helpers import is_base64_image

    previous = query_info.get("previous_conversations", [])
    if not previous:
        return []

    recent = previous[-6:]
    messages = []

    for conv in recent:
        role = conv.get("role", "")
        content = str(conv.get("content", "")).strip()

        if role == "user_query":
            parts: list[dict] = [{"type": "text", "text": content[:200]}]
            attachments = conv.get("attachments") or []
            if attachments and blob_store and org_id:
                for att in attachments:
                    if not isinstance(att, dict):
                        continue
                    mime = (att.get("mimeType") or "").lower()
                    vrid = att.get("virtualRecordId") or ""
                    if not vrid:
                        continue
                    try:
                        record = await blob_store.get_record_from_storage(vrid, org_id)
                        if not record:
                            continue
                        if mime in ["application/pdf", "text/mdx", "text/markdown", "text/plain"]:
                            parts.extend(resolve_attachment_blocks_simple(record, is_multimodal_llm))
                        elif mime.startswith("image/") and is_multimodal_llm:
                            blocks = (
                                (record.get("block_containers") or {}).get("blocks") or []
                            )
                            for block in blocks:
                                if not isinstance(block, dict) or block.get("type") != "image":
                                    continue
                                data = block.get("data")
                                uri = (
                                    data.get("uri", "") if isinstance(data, dict)
                                    else (data if isinstance(data, str) else "")
                                )
                                if uri and is_base64_image(uri):
                                    parts.append(
                                        {"type": "image_url", "image_url": {"url": uri}}
                                    )
                    except Exception:
                        pass  # Never let attachment fetching block routing
            # Avoid wrapping in a list when there are no visual blocks
            msg_content: Any = content[:200] if len(parts) == 1 else parts
            messages.append(HumanMessage(content=msg_content))

        elif role == "bot_response":
            first_line = content.split("\n")[0][:150]
            messages.append(AIMessage(content=first_line))

    return messages


def build_tier_rubric(capability_block: str, sql_verify_override: str, n_knowledge: int) -> str:
    """Builds the quick/react/deep tier-classification system prompt from
    already-computed capability context.

    Extracted out of `classify_route()` unchanged (byte-identical output for
    the same inputs) so `app.agents.agent_loop.intent.parse_intent_and_route`
    can reuse the SAME tier rubric for its merged intent+route call without
    the two prompts drifting apart — see that module's docstring.
    """
    return (
        "You are a routing agent. Classify the user request into exactly one "
        "execution tier: quick, react, or deep.\n\n"

        + capability_block
        + sql_verify_override
        + "## quick\n"
        "Every action and every parameter can be fully determined right now "
        "from the query and context, before anything runs. The request itself "
        "is the final action — retrieving, searching, displaying, or acting on "
        "something where the goal is the retrieval or action itself, not "
        "further processing of what comes back.\n\n"

        "CRITICAL: For a request to be 'quick', ALL of the following must be true:\n"
        "1. ALL required parameters for the final action are directly available "
        "from the query text, conversation context, or system constants — NO "
        "tool calls needed to obtain any parameter (IDs, keys, identifiers).\n"
        "2. The query contains exactly ONE distinct action or question. If the "
        "query asks about two or more separate topics, tasks, or actions "
        "(e.g., 'How do I do X and also Y?'), it is NOT quick.\n\n"

        "## react\n"
        "A fixed, predictable sequence of dependent steps where the chain "
        "length is deterministic before execution starts, but at least one "
        "step's parameters only become known from a prior step's result. The "
        "intent implies: get something first, then do something with it — "
        "where 'it' is one specific thing.\n\n"
        "Key indicator: If the final action requires a parameter (ID, key, "
        "identifier, or any structured value) that must be fetched/resolved "
        "through a tool call, this is react. The dependency chain is: "
        "resolve parameter → execute final action.\n\n"
        "Also use react when the query has multiple related sub-tasks that "
        "build on shared context.\n\n"
        "**react is the safe default when routing is unclear.**\n\n"

        "## deep\n"
        "Reserved for tasks react cannot handle. Only two cases qualify:\n"
        "(a) The intent requires getting a collection and then doing something "
        "to EVERY item in it — the number of items is unknown before the "
        "collection is retrieved. Wanting to SEE a collection is not this.\n"
        "(b) The intent requires gathering information from ≥2 fully "
        "independent sources and combining it into one unified answer.\n"
        f"Configuration check: {n_knowledge} source(s) configured — deep "
        f"is {'viable' if n_knowledge >= 2 else 'NOT viable (need ≥2)'}.\n\n"

        "## What counts as a known vs unknown parameter\n"
        "Known (does NOT require a prior tool call):\n"
        "  • Any search term, keyword, or topic that appears in the query text "
        "itself — the user's words ARE the search input.\n"
        "  • Any ID, name, key, or value explicitly stated in the query or "
        "conversation history.\n"
        "  • Which tool or knowledge source to use — this is an internal agent "
        "routing decision, NOT a parameter the query must supply.\n\n"
        "Unknown (DOES require a prior tool call):\n"
        "  • An ID, key, or identifier that is not present anywhere in the "
        "query or conversation and must be obtained from a tool's response "
        "before the final action can execute.\n\n"

        "## Decision\n"
        "Answer these in order. Stop at the first match.\n\n"

        "Q1: Is this a single question or action, AND are ALL required "
        "parameters known (per the definitions above) — with NO tool calls "
        "needed to obtain them? → **quick**\n\n"

        "Q2: Does the request require a fixed sequence where at least one "
        "parameter for the final action must come from a prior tool's result? "
        "→ **react**\n\n"

        "Q3: Does the request imply acting on every item in a collection "
        "whose size is only known at runtime, or combining ≥2 fully "
        f"independent sources ({n_knowledge} configured)? → **deep**\n\n"

        "Q4: Does the query contain multiple distinct sub-questions, topics, "
        "or actions? → NOT quick; use react (if topics are related or "
        "sequential) or deep (if fully independent and targeting different "
        "sources).\n\n"

        "Default → **react**\n\n"

        "For follow-ups ('yes', 'ok', 'do it', 'give all', 'show more', "
        "'proceed') — infer the full intent from the conversation history "
        "above, then apply the decision tree to that inferred intent."
    )


def build_sql_verify_override(query_info: dict[str, Any]) -> str:
    """Extracted from `classify_route()` — SQL toolsets always force
    `react` regardless of tier, since schema introspection + intermediate
    verification is required before any SQL execution."""
    toolsets = query_info.get("toolsets") or []
    has_sql_toolset = any(
        isinstance(ts, dict)
        and any(name in ts.get("name", "").lower() for name in ("mariadb", "redshift"))
        for ts in toolsets
    )
    if not has_sql_toolset:
        return ""
    return (
        "## SQL override (highest priority)\n"
        "This agent has SQL database tools (MariaDB and/or Redshift) "
        "configured. SQL queries require schema introspection before "
        "execution and verification of intermediate results, so any "
        "request that may touch these tools MUST be routed to **react**. "
        "This rule overrides the tier definitions below — do NOT choose "
        "`quick` or `deep` when SQL tools are involved.\n\n"
    )


async def classify_route(
    query_info: dict[str, Any],
    logger: Logger,
    llm: BaseChatModel,
    *,
    config_service: Any = None,
    graph_provider: Any = None,
    is_multimodal_llm: bool = False,
    org_id: str = "",
    opik_tracer: Any = None,
) -> RouteDecision:
    """
    Classify a query into one of three agent tiers: quick, react, or deep.
    Falls back to 'react' if the query is empty or the LLM call/parsing fails.
    """
    user_query = query_info.get("query", "").strip()
    if not user_query:
        return RouteDecision(reasoning="empty query", route="react")

    capability_block, n_knowledge, _indexed_connectors, _kb_sources, _tools_data = (
        build_capability_context(query_info)
    )
    sql_verify_override = build_sql_verify_override(query_info)

    # Create blob_store once; reused for both history attachments and current ones.
    blob_store = None
    if config_service and graph_provider:
        try:
            blob_store = BlobStorage(
                logger=logger,
                config_service=config_service,
                graph_provider=graph_provider,
            )
        except Exception as _bs_exc:
            logger.warning("Router: failed to create blob_store: %s", _bs_exc)

    prior_messages = await build_prior_routing_messages(
        query_info,
        blob_store=blob_store,
        org_id=org_id,
        is_multimodal_llm=is_multimodal_llm,
    )

    structured_llm = llm.with_structured_output(RouteDecision)

    system_prompt = build_tier_rubric(capability_block, sql_verify_override, n_knowledge)

    # Build the routing HumanMessage: the user query goes here so multimodal
    # models receive both text and image blocks in the same turn.
    # Prior-turn attachments are already carried by prior_messages in order.
    routing_human_content: Any = f"user query : {user_query}"
    attachments = query_info.get("attachments") or []
    if blob_store:
        try:
            attachment_blocks: list[dict] = []
            if attachments and is_multimodal_llm:
                attachment_blocks = await resolve_attachments(
                    attachments=attachments,
                    blob_store=blob_store,
                    org_id=org_id,
                    is_multimodal_llm=True,
                    logger=logger,
                )
            if attachment_blocks:
                routing_human_content = [
                    {"type": "text", "text": f"user query : {user_query}\n\nAttached files from the user:\n"},
                    *attachment_blocks,
                ]
        except Exception as exc:
            logger.warning("Router: failed to resolve attachments for routing context: %s", exc)

    try:
        invoke_config = {"callbacks": [opik_tracer]} if opik_tracer else {}

        decision: RouteDecision = await structured_llm.ainvoke(
            [
                SystemMessage(content=system_prompt),
                *prior_messages,
                HumanMessage(content=routing_human_content),
            ],
            config=invoke_config,
        )

        logger.info(
            "Query route: %s | (query=%s, reasoning=%s)",
            decision.route,
            user_query[:80],
            decision.reasoning[:120],
        )
        return decision

    except Exception as e:
        logger.warning("Query route: react (fallback) | router failed: %s", e)
        return RouteDecision(reasoning=f"router failed: {e}", route="react")


__all__ = [
    "RouteDecision",
    "build_capability_context",
    "build_prior_routing_messages",
    "build_sql_verify_override",
    "build_tier_rubric",
    "classify_route",
]
