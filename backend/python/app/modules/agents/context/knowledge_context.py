"""Knowledge-routing context builder, extracted from
`modules/agents/qna/nodes.py` (Phase 0 of the agent-loop migration).
"""


from __future__ import annotations

import logging
from typing import Any

from app.modules.agents.capability_summary import (
    build_connector_routing_rules,
    classify_knowledge_sources,
)
from app.modules.agents.qna.chat_state import ChatState


def _build_knowledge_context(state: ChatState, log: logging.Logger) -> str:
    """
    Build knowledge context for the planner prompt.

    Uses shared `classify_knowledge_sources` and `build_connector_routing_rules`
    from capability_summary so connector routing logic is maintained in one place.

    Derives guidance from what is actually configured:
      - agent_knowledge  → what is indexed (retrieval sources)
      - agent_toolsets   → what live API tools exist
    """
    agent_knowledge: list = state.get("agent_knowledge", []) or []
    agent_toolsets: list  = state.get("agent_toolsets", []) or []

    if not agent_knowledge:
        return ""

    # ── 1. Classify knowledge sources (shared utility) ────────────────────
    connector_configs = state.get("connector_configs") or {}
    sources = classify_knowledge_sources(
        agent_knowledge,
        connector_configs=connector_configs if isinstance(connector_configs, dict) else None,
    )
    kb_sources = [s for s in sources if s["source_type"] == "kb"]
    indexed_apps = [s for s in sources if s["source_type"] == "app"]
    indexed_type_keys = {a["type_key"] for a in indexed_apps if a["type_key"]}

    # ── 2. Classify live API toolsets ────────────────────────────────────
    api_tools_by_type: dict[str, list[str]] = {}

    for ts in agent_toolsets:
        if not isinstance(ts, dict):
            continue
        ts_name = (ts.get("name") or "").strip().lower()
        if not ts_name or ts_name in ("retrieval", "calculator"):
            continue

        ts_key   = ts_name.split()[0]
        ts_tools = ts.get("tools", [])
        tool_names = [
            t.get("fullName") or f"{ts_key}.{t.get('toolName') or t.get('name', '')}"
            for t in ts_tools
            if isinstance(t, dict)
        ]
        if not tool_names:
            tool_names = [f"{ts_key}.*"]

        api_tools_by_type.setdefault(ts_key, []).extend(tool_names)

    overlapping_keys = indexed_type_keys & set(api_tools_by_type.keys())

    # ── 3. Build context block ────────────────────────────────────────────
    lines: list[str] = [
        "",
        "## 🧠 KNOWLEDGE & DATA SOURCES",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    # --- Indexed knowledge (retrieval) ---
    if kb_sources or indexed_apps:
        lines.append(
            "\n### 📚 INDEXED KNOWLEDGE → `retrieval.search_internal_knowledge`"
        )
        lines.append(
            "Retrieval performs **semantic search** across indexed sources.\n"
            "Use it when the query asks *what is / find / search by topic or keyword*.\n"
            "⚠️  Retrieval returns a **snapshot** — it may lag behind the live system.\n\n"
            "**One filter parameter for every source, KB or app connector alike:**\n"
            "  • `connector_ids` → filters to a specific KB collection OR app connector "
            "(use the id listed below — a KB collection's id IS its connector_id)\n"
            "  • Omit it → searches all indexed content"
        )

        if kb_sources:
            lines.append("\n**Knowledge Base Collections** (search with `connector_ids`):")
            for kb in kb_sources:
                cid = kb.get("connector_id")
                if cid:
                    lines.append(
                        f'  - 📄 **{kb["label"]}** — (Collection) '
                        f'`connector_ids: ["{cid}"]`'
                    )
                else:
                    lines.append(
                        f'  - 📄 {kb["label"]} '
                        "(omit connector_ids to search full KB)"
                    )

        if indexed_apps:
            from app.modules.agents.capability_summary import (
                format_connector_filter_lines,
            )
            lines.append(
                "\n**Indexed App Connectors** (search with `connector_ids`):"
            )
            for app in indexed_apps:
                line = f"  - 🔗 **{app['label']}** (app: {app['type_key']}) — connector_id: `{app['connector_id']}`"
                fls = format_connector_filter_lines(app.get("filters"))
                if fls:
                    line += f" [indexed: {'; '.join(fls)}]"
                lines.append(line)

        # ── Routing rules (handles KB-only, connector-only, and mixed) ──
        routing = build_connector_routing_rules(
            sources,
            call_format="planner",
        )
        if routing:
            lines.append(routing)

    # --- Live API toolsets ---
    if api_tools_by_type:
        lines.append(
            "\n### ⚡ LIVE API TOOLS → service-specific tool calls"
        )
        lines.append(
            "Use live API tools when the query needs:\n"
            "  • **Current state** — data that must be up-to-date right now\n"
            "  • **Exact lookup by ID / key** — e.g. get issue PA-123\n"
            "  • **Filtered lists** — my open tickets, this sprint, unread emails\n"
            "  • **Write actions** — create, update, delete, comment, send, assign"
        )
        for ts_key, tool_names in api_tools_by_type.items():
            MAX_TOOLS = 5
            sample = ", ".join(tool_names[:MAX_TOOLS])
            more   = f" … (+{len(tool_names) - MAX_TOOLS} more)" if len(tool_names) > MAX_TOOLS else ""
            lines.append(f"  - 🛠️ **{ts_key.capitalize()}**: {sample}{more}")

    # --- Overlap guidance (apps with BOTH indexed AND live API) ---
    if overlapping_keys:
        lines.append(
            "\n### 🔀 DUAL-SOURCE APPS — Use the right source(s) for the intent"
        )
        lines.append(
            "These apps have **BOTH** indexed content (searchable via retrieval) **AND** live API tools.\n"
            "Choose based on what the user actually wants:\n"
            "\n"
            "| User intent | What to use |\n"
            "|---|---|\n"
            "| **SERVICE NOUN** — '[topic] tickets', '[topic] pages' (no explicit verb) | BOTH retrieval + live API search (parallel) |\n"
            "| **FIND / SEARCH** content by topic or keyword | BOTH retrieval + live API search (parallel) |\n"
            "| **LIVE / CURRENT** data — 'list my open tickets', 'assigned to me' | live API only |\n"
            "| **LOOKUP** by exact ID or key — PA-123, page id 12345 | live API only |\n"
            "| **WRITE ACTION** — create, update, delete, comment, assign | live API write tool only |\n"
            "| **INFORMATION** — 'what is X', 'explain Z' (no service resource noun) | retrieval only |\n"
        )
        for key in sorted(overlapping_keys):
            label = next(
                (a["label"] for a in indexed_apps if a["type_key"] == key),
                key.capitalize()
            )
            tool_sample = api_tools_by_type.get(key, [])[:4]
            lines.append(
                f"  **{label}**: retrieval → topic/historical search; "
                f"live API ({', '.join(tool_sample)}) → current data, exact IDs, write actions; "
                f"BOTH → service resource noun ('[topic] tickets', '[topic] pages') "
                f"OR explicit find/search by topic"
            )

    # --- Hybrid search guidance ---
    has_retrieval = bool(sources)
    non_overlap_search_tools: dict[str, list[str]] = {}
    for ts_key, tool_names in api_tools_by_type.items():
        search_tools = [t for t in tool_names if "search" in t.split(".")[-1].lower()]
        if search_tools:
            non_overlap_search_tools[ts_key] = search_tools

    if has_retrieval and non_overlap_search_tools:
        lines.append(
            "\n### 🔍 HYBRID SEARCH — when to combine retrieval + live search APIs"
        )
        lines.append(
            "Use **BOTH** `retrieval.search_internal_knowledge` AND a live search API **in parallel** when:\n"
            "  • User uses a **service resource noun** — 'tickets', 'issues', 'bugs', 'epics', 'pages', 'spaces' — even without an explicit verb\n"
            "  • Example: '[topic] tickets', '[topic] issues', '[topic] pages' → use BOTH retrieval + the matching service search API\n"
            "  • User explicitly asks to **FIND or SEARCH** content in a specific service\n"
            "  • User asks 'find pages/tickets/docs about [topic]'\n"
            "  • User asks 'search [app] for [X]' or 'is there anything about [topic] in [app]'\n"
            "\n"
            "**Available live search APIs:**"
        )
        for ts_key, search_tools in sorted(non_overlap_search_tools.items()):
            tool_list = ", ".join(f"`{t}`" for t in search_tools[:4])
            lines.append(f"  - 🔍 **{ts_key.capitalize()}**: {tool_list}")

        lines.append(
            "\n**EXAMPLE** — 'find pages about OneDrive configuration':\n"
            '```json\n'
            '[\n'
            '  {"name": "retrieval.search_internal_knowledge", "args": {"query": "OneDrive configuration"}},\n'
            '  {"name": "confluence.search_content", "args": {"query": "OneDrive configuration"}}\n'
            ']\n'
            '```\n'
            "**EXAMPLE** — 'what is our OneDrive configuration?' (information only):\n"
            '```json\n'
            '[{"name": "retrieval.search_internal_knowledge", "args": {"query": "OneDrive configuration"}}]\n'
            '```'
        )

    # --- Universal decision rule (always shown at the end) ---
    lines.append(
        "\n### 🎯 TOOL SELECTION SUMMARY\n"
        "```\n"
        "Greeting / thanks / meta-question about conversation                           →  can_answer_directly: true\n"
        "Write action (create/update/delete/send/assign)                                →  live API write tool\n"
        "Live/current data (list mine, open, this sprint, recent)                       →  live API read tool\n"
        "Lookup by exact ID or key (e.g. PA-123)                                        →  live API read tool\n"
        "[topic] tickets / [topic] issues / [topic] pages (service noun, dual-source)   →  BOTH retrieval + live search API (parallel)\n"
        "FIND/SEARCH [service] content by topic or keyword                              →  BOTH retrieval + live search API (parallel)\n"
        "External website/URL (Wikipedia, SO, GitHub, MDN, any public site, or a URL)   →  web_search / fetch_url\n"
        "General information query — 'what is X', 'tell me about Y' (no service noun)   →  retrieval (DEFAULT)\n"
        "Ambiguous / unclear intent                                                     →  retrieval (DEFAULT)\n"
        "```\n"
        "⚠️ **RETRIEVAL CONNECTOR RULE**:\n"
        "   • Reason about the query to determine which connector(s) it targets.\n"
        "   • **Connector(s) identified → search only those** — one parallel call per identified connector.\n"
        "   • **Cannot identify a connector (general / ambiguous) → search ALL configured connectors in parallel.**\n"
        "   • Default when uncertain: search ALL connectors.\n"
        "   • Each call sets `connector_ids` to exactly ONE connector_id — never combine them.\n"
        "   • If only KB sources are indexed (no app connectors), omit `connector_ids`.\n"
        "   • NEVER set `connector_ids` to a live-API-only service connector.\n"
        "\n"
        "⚠️ **EFFICIENCY**: If a previous tool already returned IDs/keys, use them\n"
        "   directly in the next write tool. Do NOT re-fetch items you already have."
    )

    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def _build_orchestrator_knowledge_context(state: Any, log: logging.Logger) -> str:
    """Build knowledge context for the deep-agent orchestrator prompt.

    Operates on `DeepAgentState` (task-oriented, `can_answer_directly` flow)
    rather than the ReAct `ChatState` that `_build_knowledge_context` above
    targets — the two prompts have diverged in structure and audience, so
    this is kept as a distinct function (moved here verbatim from
    `modules/agents/deep/orchestrator.py` in Phase 0) rather than merged.

    Uses shared `classify_knowledge_sources` and `build_connector_routing_rules`
    from capability_summary so the routing logic is maintained in one place.
    """
    has_knowledge = state.get("has_knowledge", False)
    has_tools = bool(state.get("tools"))

    if not has_knowledge and not has_tools:
        return (
            "## No Knowledge or Tools Configured\n"
            "This agent has no knowledge sources or service tools. "
            "For org-specific questions, inform the user to configure "
            "knowledge sources or toolsets."
        )

    if not has_knowledge:
        return (
            "## No Knowledge Base Configured\n"
            "No knowledge sources are configured for this agent. "
            "Do NOT create retrieval tasks — there is no knowledge base to search."
        )

    # ── Classify knowledge sources ─────────────────────────────────────────
    agent_knowledge: list = state.get("agent_knowledge", []) or []
    connector_configs = state.get("connector_configs") or {}
    sources = classify_knowledge_sources(
        agent_knowledge,
        connector_configs=connector_configs if isinstance(connector_configs, dict) else None,
    )

    knowledge_lines: list[str] = [
        "## Knowledge Sources Available",
        "",
        "An internal knowledge base is configured with indexed documents.",
        "",
        "**MANDATORY RULE**: When a knowledge base is available you MUST set "
        "`can_answer_directly: false` and create retrieval task(s) for ANY substantive "
        "question — even if you believe you already know the answer. The knowledge base "
        "contains organisation-specific content your training data does not have. "
        "Only pure greetings and trivial arithmetic may skip retrieval. "
        "**The routing rules below still apply**: when the user explicitly names a "
        "specific connector (e.g. 'use Jira', 'from Confluence'), create retrieval "
        "tasks for ONLY that connector — do NOT search other sources.",
    ]

    # ── Routing rules with identity block (handles KB-only, connector-only, mixed) ──
    if sources:
        routing = build_connector_routing_rules(
            sources,
            call_format="orchestrator",
        )
        knowledge_lines.append(routing)
    else:
        # has_knowledge is True but no detailed sources resolved
        knowledge_lines.append(
            "\n- Internal knowledge sources are configured (details unavailable).\n"
            "  Create a generic retrieval task that searches the knowledge base."
        )

    # ── Retrieval task quality guidance ────────────────────────────────────
    knowledge_lines.append(
        "\n**Write rich retrieval task descriptions** — the description IS the "
        "instruction the retrieval sub-agent receives. Be specific:\n"
        "  • State the topic and key aspects to cover.\n"
        "  • Include the connector_id(s) and the connector label.\n"
        "  • Ask for multiple search query phrasings (different angles / synonyms).\n"
        "  Example: instead of \"Search KB for X\", write:\n"
        "  \"Search the Confluence knowledge base (connector_id: abc-123) for X. "
        "Cover features, pricing, integrations, and edition differences. "
        "Use at least 3 search queries with different phrasings.\"\n\n"
        "**Hybrid strategy**: When a service has BOTH indexed content AND live API tools "
        "(e.g., Confluence pages are indexed AND accessible via the API), create BOTH "
        "a retrieval task AND an API task in parallel — retrieval finds indexed snapshots "
        "quickly; the API fetches the latest live version."
    )

    return "\n".join(knowledge_lines)

