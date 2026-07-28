"""
Capability Summary Builder - Shared across all agent modes.

Lists configured knowledge sources and user-configured service tools.
Internal utility tools (calculator, date_calculator, etc.) are excluded
automatically — only tools from the agent's configured toolsets are shown.
Adding new internal tools requires no changes here.

Also exports the shared `classify_knowledge_sources` utility used by
the orchestrator, qna planner, and sub-agent to classify knowledge
into KB stores vs indexed app connectors without code duplication.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.modules.agents.tool_domain import derive_tool_domain


# ---------------------------------------------------------------------------
# Connector filter fetch (route handlers only — filters only, not full config)
# ---------------------------------------------------------------------------

async def fetch_connector_configs(
    config_service: Any,
    connector_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """
    Fetch sync + indexing filter *values* for each connector in parallel.

    Returns:
        {connector_id: {"sync": {...}, "indexing": {...}}}
    Per-connector errors yield {}.
    """
    ids = [
        c for c in (connector_ids or [])
        if c and isinstance(c, str)
    ]
    if not ids or not config_service:
        return {}

    async def _fetch_one(cid: str) -> tuple[str, dict[str, Any]]:
        try:
            cfg = await config_service.get_config(f"/services/connectors/{cid}/config")
            raw = (cfg or {}).get("filters", {})
            return cid, {
                "sync": raw.get("sync", {}).get("values", {}),
                "indexing": raw.get("indexing", {}).get("values", {}),
            }
        except Exception:
            return cid, {}

    pairs = await asyncio.gather(*[_fetch_one(c) for c in ids])
    return dict(pairs)


def format_connector_filter_lines(filters: dict[str, Any] | None) -> list[str]:
    """
    Human-readable filter lines for prompts.
    `filters` = {"sync": {key: {type, value}}, "indexing": {key: {type, value}}}.

    Returns up to two lines:
      - Scoped to: <list values> (<key name>); ...   (from list-type sync filters)
      - Content indexed: <key>, ...                  (from boolean-true indexing filters)
    """
    if not filters:
        return []

    lines: list[str] = []

    # Sync scope — collect list-type filters that have selected values
    sync_vals = filters.get("sync")
    if isinstance(sync_vals, dict):
        scope_parts: list[str] = []
        for key, entry in sync_vals.items():
            if not isinstance(entry, dict) or entry.get("type") != "list":
                continue
            raw = entry.get("value")
            if not isinstance(raw, list):
                continue
            labels = [
                str(item.get("label") or item.get("id")) if isinstance(item, dict)
                else str(item)
                for item in raw
                if item not in (None, "")
                and (not isinstance(item, dict) or item.get("label") or item.get("id"))
            ]
            if labels:
                scope_parts.append(f"{', '.join(labels)} ({key.replace('_', ' ')})")
        if scope_parts:
            lines.append("Scoped to: " + "; ".join(scope_parts))

    # Indexing content — keys whose boolean value is True (skip control flags)
    idx_vals = filters.get("indexing")
    if isinstance(idx_vals, dict):
        enabled = sorted(
            key.replace("_", " ")
            for key, entry in idx_vals.items()
            if not key.startswith("enable_")
            and isinstance(entry, dict)
            and entry.get("type") == "boolean"
            and entry.get("value") is True
        )
        if enabled:
            lines.append("Content indexed: " + ", ".join(enabled))

    return lines


# ---------------------------------------------------------------------------
# Shared knowledge classification utility (used by all agent modes)
# ---------------------------------------------------------------------------

def classify_knowledge_sources(
    agent_knowledge: list[dict[str, Any]],
    *,
    connector_configs: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Classify agent knowledge into a single, uniform list of retrieval sources.

    KB document stores and indexed app connectors are the SAME concept
    downstream — both are searched via search_internal_knowledge's single
    `connector_ids` parameter (there is no separate `collection_ids`) — so
    they are returned as ONE flat list, discriminated by `source_type`,
    instead of two parallel lists with two different shapes that every
    caller had to destructure and re-combine. Callers that need to display
    KB vs app sources differently (icons, headers, `type_key`/`filters`)
    filter this list by `source_type` rather than receiving two lists.

    This is the single source of truth for knowledge classification used by:
    - The deep-agent orchestrator (_build_orchestrator_knowledge_context)
    - The qna/react planner (_build_knowledge_context)
    - internal_exploration_agent's per-request playbook (domain_agents.py)
    - The router's capability summary (qna/router.py)

    Args:
        agent_knowledge: Raw knowledge list from state["agent_knowledge"]
        connector_configs: Optional map from connector_id to
            {"sync": {...}, "indexing": {...}} (from fetch_connector_configs / state).

    Returns:
        list of dicts, each:
        {"label": str, "connector_id": str, "source_type": "kb" | "app",
         "type_key": str, optional "filters": dict}
        - source_type == "kb": a KB document store. `connector_id` is ""
          when the KB has no id of its own (search the full KB, no id
          filter needed) — `type_key` is always "" for these.
        - source_type == "app": an indexed app connector, searchable via
          search_internal_knowledge's connector_ids filter. Always has a
          non-empty connector_id — entries without one are skipped.
    """
    sources: list[dict[str, Any]] = []

    for k in (agent_knowledge or []):
        if not isinstance(k, dict):
            continue

        name = k.get("displayName") or k.get("name") or ""
        ktype = (k.get("type") or "").strip()
        connector_id = (k.get("connectorId") or "").strip()

        if ktype.upper() == "KB":
            sources.append({
                "label": name or "Knowledge Base",
                "type_key": "",
                "connector_id": connector_id,
                "source_type": "kb",
            })
        elif connector_id:
            type_key = ktype.lower().split()[0] if ktype else ""
            label = name or type_key.capitalize() or "App Connector"
            entry: dict[str, Any] = {
                "label": label,
                "type_key": type_key,
                "connector_id": connector_id,
                "source_type": "app",
            }
            if connector_configs:
                fc = connector_configs.get(connector_id)
                if isinstance(fc, dict) and fc:
                    entry["filters"] = fc
            sources.append(entry)

    return sources

def build_capability_summary(state: dict[str, Any]) -> str:
    """
    Build a capability summary for the LLM to answer "what can you do?" questions.

    Shows available actions grouped by domain — service connectors AND
    built-ins (calculator, date_calculator, coding_sandbox, etc.), deduped.
    The Knowledge Sources content (record model, source table) is emitted
    separately by ``_build_knowledge_context`` / ``SourceCatalog.render()``,
    so it appears exactly once in the prompt.

    Returns "" when the agent has no tools at all — a header followed by
    "No tools configured" tells the model only what it already knows and
    just burns prompt tokens.
    """
    domains, domain_notes = _get_all_tool_domains(state)

    if not domains:
        return ""

    parts: list[str] = ["## Capability Summary", ""]

    _build_actions_section(domains=domains, domain_notes=domain_notes, parts=parts)
    _build_auth_status_section(state=state, parts=parts)

    return "\n".join(parts)


def _build_auth_status_section(state: dict[str, Any], *, parts: list[str]) -> None:
    """Append a "Needs Authentication" subsection listing toolsets this
    request found CONFIGURED but couldn't load because `ToolInstanceCreator`
    raised an auth error (`toolset_load_failures[name] == "not_authenticated"`
    — see `AgentContext.toolset_load_failures`'s docstring and
    `tool_loader.py`'s per-toolset try/except). Without this, a toolset that
    fails auth silently vanishes from the agent's tool list with no signal
    at all, so a query naming it gets a generic "I don't have that tool"
    instead of "authenticate it in Settings".

    Deliberately omitted when empty — most requests have zero auth
    failures, and an empty header would just be noise on every prompt.
    """
    failures: dict[str, Any] = state.get("toolset_load_failures") or {}
    unauthenticated = sorted(
        name for name, reason in failures.items() if reason == "not_authenticated"
    )
    if not unauthenticated:
        return

    parts.append("### Needs Authentication")
    parts.append(
        "The following toolsets are configured for this agent but are NOT "
        "authenticated yet — their tools are unavailable until the user "
        "authenticates. If the user's query targets one of these, tell them "
        "plainly that it needs authentication and to go to Settings > "
        "Toolsets to connect it, instead of saying the toolset doesn't exist:"
    )
    for name in unauthenticated:
        parts.append(f"- {name}")
    parts.append("")


def _build_actions_section(
    *,
    domains: dict[str, list[str]],
    domain_notes: dict[str, str],
    parts: list[str],
) -> None:
    """Append available actions section to parts.

    Shows all tools the runtime loads — service connectors AND built-ins
    (calculator, coding_sandbox, etc.), deduped by name.
    Notes for non-obvious built-in tools are derived from tool.description —
    no hardcoding required.

    `domains`/`domain_notes` are computed once by the caller (including the
    has_knowledge-driven retrieval/knowledgehub/knowledgegraph augmentation)
    so that `build_capability_summary` can decide whether to emit the
    section at all before rendering it.
    """
    parts.append("### Available Actions")

    if not domains:
        parts.append("- No tools configured")
        parts.append("")
        return

    for domain, actions in sorted(domains.items()):
        display = domain.replace("_", " ").title()
        note = domain_notes.get(domain, "")
        suffix = f" — {note}" if note else ""
        parts.append(f"- **{display}**: {', '.join(actions)}{suffix}")

    parts.append("")


def _extract_domain_note(description: str, max_chars: int = 130) -> str:
    """
    Extract a concise capability note from a tool's description string.

    Preference order:
    1. Clause starting with "Use this to …" or "Use ONLY for …" or "Useful for …"
       (most explicit statement of what the tool does)
    2. First sentence (up to first ". " or ".\n")

    Returns an empty string when nothing useful can be extracted.
    No hardcoded tool names — works for any tool automatically.
    """
    if not description:
        return ""

    for marker in ("Use this to ", "Use ONLY for ", "Useful for "):
        idx = description.find(marker)
        if idx == -1:
            continue
        rest = description[idx + len(marker):]
        end = len(rest)
        for sep in (". ", ".\n", "\n"):
            s = rest.find(sep)
            if 0 < s < end:
                end = s
        end = min(end, max_chars)
        note = rest[:end].strip().rstrip(".")
        if note:
            return note

    # First sentence fallback
    for sep in (". ", ".\n"):
        idx = description.find(sep)
        if 0 < idx <= max_chars:
            return description[:idx].strip()

    return description[:max_chars].strip()


def _get_all_tool_domains(
    state: dict[str, Any],
) -> tuple[dict[str, list[str]], dict[str, str]]:
    """
    Get all loaded tools grouped by domain, deduped by name.

    Returns:
        (domains, domain_notes) where:
        - domains:      {domain: [action_display, ...]}
        - domain_notes: {domain: brief_capability_note} — only for built-in
          (non-service) domains; extracted from tool.description, no hardcoding.

    Primary: get_agent_tools_with_schemas(state) — includes built-ins and all
    service connectors, already deduplicated by the tool registry.
    Fallback: state["tools"] flat list (service tools only; deduped here).
    """
    state_dotted: list[str] = [
        t for t in (state.get("tools") or [])
        if isinstance(t, str) and "." in t
    ]
    # Service domains come from configured toolsets — their names are self-explanatory
    service_domains: set[str] = {t.split(".", 1)[0] for t in state_dotted}

    try:
        from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas
        runtime_tools = get_agent_tools_with_schemas(state)
    except Exception:
        runtime_tools = []

    domains: dict[str, list[str]] = {}
    domain_notes: dict[str, str] = {}

    if runtime_tools:
        seen: set[str] = set()
        for tool in runtime_tools:
            raw = getattr(tool, "name", "")
            if not raw or raw in seen:
                continue
            seen.add(raw)

            domain, action = derive_tool_domain(tool)
            action_display = action.replace("_", " ")
            if action_display and action_display not in domains.get(domain, []):
                domains.setdefault(domain, []).append(action_display)

            # Derive a capability note from the tool's own description for
            # built-in domains — service domain names speak for themselves.
            if domain not in domain_notes and domain not in service_domains:
                note = _extract_domain_note(getattr(tool, "description", ""))
                if note:
                    domain_notes[domain] = note

        return domains, domain_notes

    # Fallback: state["tools"] flat list with deduplication (no notes available)
    seen_names: set[str] = set()
    for t in state_dotted:
        if t in seen_names:
            continue
        seen_names.add(t)
        domain, action = t.split(".", 1)
        action_display = action.replace("_", " ")
        if action_display not in domains.get(domain, []):
            domains.setdefault(domain, []).append(action_display)

    # Last resort: agent_toolsets metadata
    if not domains:
        for toolset in (state.get("agent_toolsets") or []):
            if not isinstance(toolset, dict):
                continue
            ts_name = toolset.get("name", "")
            if not ts_name:
                continue
            for t in (toolset.get("tools") or []):
                if isinstance(t, dict):
                    full = t.get("fullName") or f"{ts_name}.{t.get('name', '')}"
                    act = full.split(".", 1)[1] if "." in full else full
                    act_d = act.replace("_", " ")
                    if act_d and act_d not in domains.get(ts_name, []):
                        domains.setdefault(ts_name, []).append(act_d)
            for t in (toolset.get("selectedTools") or []):
                if isinstance(t, str):
                    act = t.split(".", 1)[1] if "." in t else t
                    act_d = act.replace("_", " ")
                    if act_d and act_d not in domains.get(ts_name, []):
                        domains.setdefault(ts_name, []).append(act_d)

    return domains, {}