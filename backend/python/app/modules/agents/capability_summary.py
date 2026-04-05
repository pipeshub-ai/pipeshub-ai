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
    Skips knowledgeBase_* pseudo-IDs. Per-connector errors yield {}.
    """
    ids = [
        c for c in (connector_ids or [])
        if c and isinstance(c, str) and not c.startswith("knowledgeBase_")
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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Classify agent knowledge into KB document stores and indexed app connectors.

    This is the single source of truth for knowledge classification used by:
    - The deep-agent orchestrator (_build_knowledge_context in orchestrator.py)
    - The qna/react planner (_build_knowledge_context in nodes.py)
    - The sub-agent tool guidance (_build_sub_agent_tool_guidance in sub_agent.py)

    Args:
        agent_knowledge: Raw knowledge list from state["agent_knowledge"]
        connector_configs: Optional map from connector_id to
            {"sync": {...}, "indexing": {...}} (from fetch_connector_configs / state).

    Returns:
        (kb_sources, indexed_connectors) where:
        - kb_sources: list of dicts {"label": str, "collection_ids": list[str]}
          collection_ids is extracted from filters.recordGroups and passed as the
          `collection_ids` parameter in search_internal_knowledge calls.
          An empty collection_ids list means no specific filter (search full KB).
        - indexed_connectors: list of dicts
          {"label": str, "type_key": str, "connector_id": str, optional "filters": dict}
          representing app connectors whose content is indexed and
          searchable via search_internal_knowledge with the connector_ids filter.
    """
    kb_sources: list[dict[str, Any]] = []
    indexed_connectors: list[dict[str, Any]] = []

    for k in (agent_knowledge or []):
        if not isinstance(k, dict):
            continue

        name = k.get("displayName") or k.get("name") or ""
        ktype = (k.get("type") or "").strip()
        connector_id = (k.get("connectorId") or "").strip()

        if ktype.upper() == "KB":
            # Extract record group IDs from filters — these are passed as
            # collection_ids in search_internal_knowledge calls.
            filters_data = k.get("filters", {})
            if isinstance(filters_data, str):
                import json as _json
                try:
                    filters_data = _json.loads(filters_data)
                except (ValueError, _json.JSONDecodeError):
                    filters_data = {}
            record_groups = (
                [g for g in filters_data.get("recordGroups", []) if g]
                if isinstance(filters_data, dict)
                else []
            )
            kb_sources.append({
                "label": name or "Knowledge Base",
                "type": "Collection",
                "collection_ids": record_groups,
            })
        elif connector_id:
            type_key = ktype.lower().split()[0] if ktype else ""
            label = name or type_key.capitalize() or "App Connector"
            entry: dict[str, Any] = {
                "label": label,
                "type_key": type_key,
                "connector_id": connector_id,
            }
            if connector_configs:
                fc = connector_configs.get(connector_id)
                if isinstance(fc, dict) and fc:
                    entry["filters"] = fc
            indexed_connectors.append(entry)

    return kb_sources, indexed_connectors


# ---------------------------------------------------------------------------
# Helpers for unique slugs / task IDs when same-type connectors exist
# ---------------------------------------------------------------------------

def _label_slug(label: str) -> str:
    """Convert a display label to a safe slug for task IDs."""
    return "".join(c if c.isalnum() else "_" for c in label.lower()).strip("_")


def _unique_connector_slug(connector: dict[str, Any], index: int) -> str:
    """
    Build a unique slug for a connector, using label first and falling back
    to index suffix to guarantee uniqueness across same-type connectors.

    E.g. two Confluence connectors named "Confluence" and "Confluence Engineering"
    → "retrieval_confluence" and "retrieval_confluence_engineering"

    Two both named "Confluence"
    → "retrieval_confluence_1" and "retrieval_confluence_2"
    """
    label = connector.get("label", "")
    type_key = connector.get("type_key", "")
    slug = _label_slug(label) if label else type_key or f"connector_{index}"
    return slug


def _deduplicate_task_ids(
    indexed_connectors: list[dict[str, Any]],
) -> list[str]:
    """
    Generate a list of unique task_id suffixes for each connector,
    appending _1, _2, etc. when slugs collide.
    """
    raw_slugs = [
        _unique_connector_slug(c, i)
        for i, c in enumerate(indexed_connectors)
    ]
    # Count occurrences
    slug_counts: dict[str, int] = {}
    for s in raw_slugs:
        slug_counts[s] = slug_counts.get(s, 0) + 1

    # Assign unique suffixes where needed
    slug_next: dict[str, int] = {}
    result: list[str] = []
    for s in raw_slugs:
        if slug_counts[s] > 1:
            idx = slug_next.get(s, 1)
            slug_next[s] = idx + 1
            result.append(f"{s}_{idx}")
        else:
            result.append(s)

    return result


def build_connector_routing_rules(
    indexed_connectors: list[dict[str, Any]],
    kb_sources: list[dict[str, Any]] | None = None,
    *,
    call_format: str = "planner",
    kb_only_note: str = "",
) -> str:
    """
    Build the retrieval source routing decision rules as a natural-language block.

    Covers TWO distinct parameter paths in search_internal_knowledge:
      • App connectors → pass `connector_ids: ["<connector_id>"]`
      • KB collections → pass `collection_ids: ["<record_group_id>"]`

    Handles multiple connectors of the same type correctly by:
      - Giving each a unique display identity (label + connector_id)
      - Generating unique task_ids for orchestrator format
      - Emphasizing that each connector is a separate source requiring
        its own individual search call

    Args:
        indexed_connectors: list from classify_knowledge_sources
            (app connectors with connector_id)
        kb_sources: list from classify_knowledge_sources
            (KB collections with optional collection_ids)
        call_format: "planner" → tool-call JSON examples
                     "orchestrator" → task-description examples
        kb_only_note: fallback text returned when both lists are empty.

    Returns:
        Formatted routing rules string, or kb_only_note if nothing configured.
    """
    _kb: list[dict[str, Any]] = kb_sources or []

    if not indexed_connectors and not _kb:
        return kb_only_note

    n_conn = len(indexed_connectors)
    n_kb   = len(_kb)
    n_total = n_conn + n_kb

    # Pre-compute unique task_id slugs for connectors
    connector_slugs = _deduplicate_task_ids(indexed_connectors)

    # ── Detect same-type duplicates for extra guidance ──────────────────────
    type_counts: dict[str, int] = {}
    for c in indexed_connectors:
        tk = c.get("type_key", "")
        type_counts[tk] = type_counts.get(tk, 0) + 1
    has_same_type_duplicates = any(v > 1 for v in type_counts.values())

    # ── Identity block ──────────────────────────────────────────────────────
    # Each source gets a unique identity line with its connector_id so the
    # LLM can distinguish same-type connectors.
    identity_lines: list[str] = []

    if _kb:
        identity_lines.append(
            "  📚 **KB Collections** (search with `collection_ids` parameter):"
        )
        for k in _kb:
            cids = k.get("collection_ids", [])
            if cids:
                cids_display = ", ".join(f'"{c}"' for c in cids)
                identity_lines.append(
                    f'    • **{k["label"]}** — `collection_ids=[{cids_display}]`'
                )
            else:
                identity_lines.append(
                    f'    • **{k["label"]}** — omit collection_ids (searches full KB)'
                )

    if indexed_connectors:
        has_filters = any(c.get("filters") for c in indexed_connectors)
        identity_lines.append(
            "  🔗 **App Connectors** (search with `connector_ids` parameter):"
        )
        if has_filters:
            identity_lines.append(
                "    ⓘ Scope and content below describe what is indexed in each connector"
                " — they do NOT restrict routing decisions."
            )
        for c in indexed_connectors:
            identity_lines.append(
                f'    • **{c["label"]}** (app: {c["type_key"]}) — connector_id: `{c["connector_id"]}`'
            )
            for fl in format_connector_filter_lines(c.get("filters")):
                identity_lines.append(f"          {fl}")

    identity_block = "\n".join(identity_lines)

    # ── Routing decision text ────────────────────────────────────────────────
    if n_conn > 0:
        # Build the "search all sources" bullet list
        all_src_lines: list[str] = []
        for k in _kb:
            cids = k.get("collection_ids", [])
            if cids:
                cids_str = ", ".join(f'"{c}"' for c in cids)
                all_src_lines.append(
                    f'     - {k["label"]} (KB): `collection_ids=[{cids_str}]`'
                )
            else:
                all_src_lines.append(
                    f'     - {k["label"]} (KB): omit collection_ids'
                )
        for c in indexed_connectors:
            all_src_lines.append(
                f'     - {c["label"]} (app: {c["type_key"]}): `connector_ids=["{c["connector_id"]}"]`'
            )
        all_src_str = "\n".join(all_src_lines)

        if has_same_type_duplicates:
            # List each duplicated app type with its connectors so the LLM
            # knows exactly which groups need signal-based disambiguation.
            dup_type_groups: dict[str, list[dict]] = {}
            for c in indexed_connectors:
                if type_counts[c["type_key"]] > 1:
                    dup_type_groups.setdefault(c["type_key"], []).append(c)

            dup_note_lines = [
                "   ⚠️  The following app types have multiple connectors — you MUST",
                "       apply the signals above to identify the correct one before routing:",
            ]
            for app_type, conns in dup_type_groups.items():
                conn_entries = ", ".join(
                    f'**{c["label"]}** (connector_id: `{c["connector_id"]}`)'
                    for c in conns
                )
                dup_note_lines.append(f"       • app: {app_type} → {conn_entries}")
            same_type_note = "\n" + "\n".join(dup_note_lines) + "\n"

            rule3 = (
                f"   3. **App type is named** but signals could not identify a specific connector\n"
                f"      → Search **all connectors of that app type** in parallel. Do NOT include\n"
                f"        other app types or KB sources.\n\n"
            )
            rule4_num = "4"
        else:
            same_type_note = ""
            rule3 = ""
            rule4_num = "3"

        routing_decision = (
            f"\n**How to route retrieval — follow these steps every time:**\n\n"
            f"⚠️ **Scope signals tell you WHERE to search — they never mean 'skip retrieval'.**\n"
            f"Even if a signal clearly identifies one connector, you MUST still plan a retrieval\n"
            f"call to that connector. Identifying the target is step 1; executing retrieval is step 2.\n\n"
            f"**Step 1 — Analyse the query and conversation context for targeting signals:**\n"
            f"   • Scope identifiers — scope values, project keys, space names, labels, or\n"
            f"     identifiers that appear in a connector's 'Scoped to' field above.\n"
            f"     ⚠️ A scope name in the query (e.g. 'Software Development') means 'search that\n"
            f"        connector for this topic' — NOT 'answer directly without searching'.\n"
            f"   • Content type — specific content types requested that only some connectors index\n"
            f"     (compare against each connector's 'Content indexed' field above).\n"
            f"   • Connector label — exact or partial/fuzzy match against a connector's name.\n"
            f"   • Domain or topic clues — business area, team, or subject that clearly aligns\n"
            f"     with a specific connector's indexed scope.\n"
            f"   • Conversation context — earlier messages that established which connector or\n"
            f"     source was being discussed.\n"
            f"{same_type_note}"
            f"\n**Step 2 — Route based on what Step 1 found:**\n\n"
            f"   1. Signals clearly identify **one specific connector**\n"
            f"      → Search **that connector ONLY**. Do NOT add tasks for other sources.\n\n"
            f"   2. Signals identify **multiple specific connectors**\n"
            f"      → One parallel search task per identified connector.\n\n"
            f"{rule3}"
            f"   {rule4_num}. No signals found / truly ambiguous → search ALL {n_total} source(s) in parallel:\n"
            f"{all_src_str}\n\n"
            f"**Parameter rules (CRITICAL — never mix these up):**\n"
            f"   • KB collection  → `collection_ids: [\"<record_group_id>\"]`"
            f"  — NEVER use connector_ids for a KB\n"
            f"   • App connector  → `connector_ids: [\"<connector_id>\"]`"
            f"  — NEVER use collection_ids for a connector\n"
            f"   • One call per source — never combine IDs in one call; all calls run in parallel\n"
            f"   • **Default when truly uncertain: search ALL {n_total} source(s)**\n"
        )
    else:
        # KB-only: no app connectors configured
        routing_decision = (
            f"\n**KB-only configuration** — no app connectors configured:\n\n"
            f"1. Search every KB collection listed above for ALL substantive queries.\n"
            f"2. **Parameter rules:**\n"
            f"   • KB with collection_ids → set `collection_ids: [\"<record_group_id>\"]`\n"
            f"   • KB without collection_ids → omit both collection_ids and connector_ids\n\n"
        )

    # ── Call-format examples ─────────────────────────────────────────────────
    if call_format == "planner":
        all_parts: list[str] = []
        for k in _kb:
            cids = k.get("collection_ids", [])
            if cids:
                cids_str = ", ".join(f'"{c}"' for c in cids)
                all_parts.append(
                    f'    {{"name": "retrieval.search_internal_knowledge",'
                    f' "args": {{"query": "<your query>", "collection_ids": [{cids_str}]}}}}'
                )
            else:
                all_parts.append(
                    f'    {{"name": "retrieval.search_internal_knowledge",'
                    f' "args": {{"query": "<your query>", "collection_ids": null}}}}'
                )
        for c in indexed_connectors:
            all_parts.append(
                f'    {{"name": "retrieval.search_internal_knowledge",'
                f' "args": {{"query": "<your query>", "connector_ids": ["{c["connector_id"]}"]}}}}',
            )
        all_calls = ",\n".join(all_parts)

        # Specific-source example: prefer first connector, fall back to first KB
        if indexed_connectors:
            c0 = indexed_connectors[0]
            specific = (
                f'    {{"name": "retrieval.search_internal_knowledge",'
                f' "args": {{"query": "<your query>", "connector_ids": ["{c0["connector_id"]}"]}}}}',
            )[0]
        elif _kb and _kb[0].get("collection_ids"):
            cids_str = ", ".join(f'"{c}"' for c in _kb[0]["collection_ids"])
            specific = (
                f'    {{"name": "retrieval.search_internal_knowledge",'
                f' "args": {{"query": "<your query>", "collection_ids": [{cids_str}]}}}}'
            )
        else:
            specific = (
                f'    {{"name": "retrieval.search_internal_knowledge",'
                f' "args": {{"query": "<your query>", "collection_ids": null}}}}'
            )

        if n_total > 1:
            example_section = (
                f"\n  **All sources (general / ambiguous query):**\n"
                "  ```json\n  [\n"
                f"{all_calls}\n"
                "  ]\n  ```\n"
                f"  **Specific source only (when intent is clear):**\n"
                "  ```json\n  [\n"
                f"{specific}\n"
                "  ]\n  ```"
            )
        else:
            example_section = (
                f"\n  **Call format:**\n"
                "  ```json\n  [\n"
                f"{all_calls}\n"
                "  ]\n  ```"
            )

    else:
        # Orchestrator format — task description examples
        # Uses deduplicated slugs so same-type connectors get unique task_ids
        all_parts: list[str] = []
        for k in _kb:
            slug = _label_slug(k["label"])
            cids = k.get("collection_ids", [])
            if cids:
                cids_esc = ", ".join(f'\\"{c}\\"' for c in cids)
                all_parts.append(
                    f'    {{"task_id": "retrieval_kb_{slug}",'
                    f' "description": "Search the {k["label"]} knowledge base'
                    f' (collection_ids: [{cids_esc}]) for <topic>.'
                    f' Call search_internal_knowledge with collection_ids: [{cids_esc}].",'
                    f' "domains": ["retrieval"], "depends_on": []}}'
                )
            else:
                all_parts.append(
                    f'    {{"task_id": "retrieval_kb_{slug}",'
                    f' "description": "Search the {k["label"]} knowledge base for <topic>.'
                    f' Call search_internal_knowledge (omit collection_ids and connector_ids).",'
                    f' "domains": ["retrieval"], "depends_on": []}}'
                )
        for i, c in enumerate(indexed_connectors):
            task_slug = connector_slugs[i]
            fls = format_connector_filter_lines(c.get("filters"))
            filt = (" Indexing scope: " + " ".join(fls).replace("\\", "\\\\").replace('"', '\\"') + ".") if fls else ""
            all_parts.append(
                f'    {{"task_id": "retrieval_{task_slug}",'
                f' "description": "Search the {c["label"]} connector'
                f' (connector_id: {c["connector_id"]}) for <topic>.{filt}'
                f' Call search_internal_knowledge with connector_ids: [\\"{c["connector_id"]}\\"].",'
                f' "domains": ["retrieval"], "depends_on": []}}'
            )
        all_tasks = ",\n".join(all_parts)

        # Specific-source example
        if indexed_connectors:
            c0 = indexed_connectors[0]
            slug0 = connector_slugs[0]
            fls0 = format_connector_filter_lines(c0.get("filters"))
            filt0 = (" Indexing scope: " + " ".join(fls0).replace("\\", "\\\\").replace('"', '\\"') + ".") if fls0 else ""
            specific = (
                f'    {{"task_id": "retrieval_{slug0}",'
                f' "description": "Search the {c0["label"]} connector'
                f' (connector_id: {c0["connector_id"]}) for <topic>.{filt0}'
                f' Call search_internal_knowledge with connector_ids: [\\"{c0["connector_id"]}\\"].",'
                f' "domains": ["retrieval"], "depends_on": []}}'
            )
        elif _kb:
            k0   = _kb[0]
            slug = _label_slug(k0["label"])
            cids = k0.get("collection_ids", [])
            if cids:
                cids_esc = ", ".join(f'\\"{c}\\"' for c in cids)
                specific = (
                    f'    {{"task_id": "retrieval_kb_{slug}",'
                    f' "description": "Search the {k0["label"]} knowledge base'
                    f' (collection_ids: [{cids_esc}]) for <topic>.'
                    f' Call search_internal_knowledge with collection_ids: [{cids_esc}].",'
                    f' "domains": ["retrieval"], "depends_on": []}}'
                )
            else:
                specific = (
                    f'    {{"task_id": "retrieval_kb_{slug}",'
                    f' "description": "Search the {k0["label"]} knowledge base for <topic>.'
                    f' Call search_internal_knowledge without filters.",'
                    f' "domains": ["retrieval"], "depends_on": []}}'
                )
        else:
            specific = ""

        if n_total > 1:
            example_section = (
                f"\n  **All sources (general / ambiguous query):**\n"
                "  ```json\n  [\n"
                f"{all_tasks}\n"
                "  ]\n  ```\n"
                f"  **Specific source only (when intent is clear):**\n"
                "  ```json\n  [\n"
                f"{specific}\n"
                "  ]\n  ```"
            )
        else:
            example_section = (
                f"\n  **Task format:**\n"
                "  ```json\n  [\n"
                f"{all_tasks}\n"
                "  ]\n  ```"
            )

    n_kb_label = f"{n_kb} KB collection{'s' if n_kb != 1 else ''}"
    n_conn_label = f"{n_conn} connector{'s' if n_conn != 1 else ''}"
    src_label = f"{n_kb_label} + {n_conn_label}" if n_kb and n_conn else (n_kb_label if n_kb else n_conn_label)
    rules = (
        f"\n**Retrieval Routing ({n_total} source(s): {src_label}):**\n\n"
        f"Configured sources:\n{identity_block}\n"
        f"{routing_decision}"
        f"{example_section}"
    )
    return rules


def build_capability_summary(state: dict[str, Any]) -> str:
    """
    Build a capability summary for the LLM to answer "what can you do?" questions.

    Shows:
    - Configured knowledge sources (from agent_knowledge)
    - User-configured service tools grouped by domain (from state["tools"] / agent_toolsets)
    - Retrieval capability (when knowledge is configured)

    Internal utility tools (calculator, date_calculator, etc.) are NOT shown —
    they are implementation details, not user-facing capabilities.
    """
    parts: list[str] = ["## Capability Summary", ""]

    has_knowledge = state.get("has_knowledge", False)

    _build_knowledge_section(state=state, has_knowledge=has_knowledge, parts=parts)
    _build_actions_section(state=state, has_knowledge=has_knowledge, parts=parts)

    parts.append(
        "When users ask about your capabilities, what you can do, what tools or "
        "knowledge you have, answer based on this summary. Do not call tools to "
        "answer capability questions."
    )

    return "\n".join(parts)


def _build_knowledge_section(
    state: dict[str, Any],
    *,
    has_knowledge: bool,
    parts: list[str],
) -> None:
    """Append knowledge sources section to parts."""
    parts.append("### Knowledge Sources")

    if not has_knowledge:
        parts.append("- No knowledge sources configured")
        parts.append("")
        return

    knowledge_list = state.get("agent_knowledge", []) or []
    connector_configs = state.get("connector_configs") or {}
    if knowledge_list:
        for kb in knowledge_list:
            if not isinstance(kb, dict):
                continue
            kb_name = kb.get("name") or kb.get("displayName") or "Unnamed"
            kb_type_raw = kb.get("type", "")
            kb_type = kb_type_raw.strip()
            connector_id = kb.get("connectorId", "")
            is_kb = kb_type.upper() == "KB"
            # Normalize to lowercase for display (raw data may be "Confluence", "JIRA", etc.)
            kb_type_display = kb_type.lower().split()[0] if kb_type and not is_kb else kb_type

            # Source header line
            if is_kb:
                parts.append(f"\n📄 **{kb_name}** (Collection)")
            elif kb_type_display:
                parts.append(f"\n🔗 **{kb_name}** (app: {kb_type_display})")
            else:
                parts.append(f"\n🔗 **{kb_name}**")

            # Extract record group IDs from filters
            filters_data = kb.get("filters", {})
            if isinstance(filters_data, str):
                import json as _json
                try:
                    filters_data = _json.loads(filters_data)
                except (ValueError, _json.JSONDecodeError):
                    filters_data = {}
            record_groups = filters_data.get("recordGroups", []) if isinstance(filters_data, dict) else []

            # Sub-bullets: browse + search parameters, then indexed scope/content
            if is_kb and record_groups:
                for rg_id in record_groups:
                    parts.append(
                        f'  - Browse: `list_files(parent_id="{rg_id}", parent_type="recordGroup")`'
                    )
                    parts.append(f'  - Search: `record_group_ids=["{rg_id}"]`')
            elif connector_id:
                parts.append(
                    f'  - Browse: `list_files(parent_id="{connector_id}", parent_type="app")`'
                )
                parts.append(f'  - Search: `connector_ids=["{connector_id}"]`')
                if isinstance(connector_configs, dict) and connector_id:
                    fc = connector_configs.get(connector_id)
                    if isinstance(fc, dict) and fc:
                        for fl in format_connector_filter_lines(fc):
                            parts.append(f"  - {fl}")
    else:
        parts.append("- Internal knowledge sources configured")

    parts.append("\n- Can search indexed documents, policies, and organizational information")

    # If agent has BOTH knowledge sources and service tools for the same app,
    # guide the LLM to use both for comprehensive results
    service_tools = state.get("tools") or []
    if knowledge_list and service_tools:
        # Check if any knowledge source overlaps with a service tool domain
        knowledge_types = {
            (kb.get("type", "")).lower()
            for kb in knowledge_list
            if isinstance(kb, dict)
        }
        tool_domains = {
            t.split(".", 1)[0].lower()
            for t in service_tools
            if isinstance(t, str) and "." in t
        }
        if knowledge_types & tool_domains or knowledge_types - {"kb", ""}:
            parts.append(
                "\n**IMPORTANT**: When listing or browsing files/documents, use BOTH:\n"
                "  - `knowledgehub.list_files` for indexed files with metadata from the Knowledge Hub\n"
                "  - Service search/list tools for live data directly from connectors\n"
                "  This gives the most complete picture."
            )

    parts.append("")


def _build_actions_section(
    state: dict[str, Any],
    *,
    has_knowledge: bool,
    parts: list[str],
) -> None:
    """Append available actions section to parts.

    Only shows user-configured service tools (from toolsets) and retrieval.
    Internal utility tools are excluded automatically.
    """
    service_domains = _get_service_tool_domains(state)

    # Add knowledge tools as visible actions when knowledge is configured
    if has_knowledge:
        service_domains.setdefault("retrieval", []).append(
            "retrieval.search_internal_knowledge",
        )
        service_domains.setdefault("knowledgehub", []).append(
            "knowledgehub.list_files",
        )

    parts.append("### Available Actions")

    if not service_domains:
        parts.append("- No tools configured")
        parts.append("")
        return

    for domain, tool_names in sorted(service_domains.items()):
        display = domain.replace("_", " ").title()
        actions = [
            (t.split(".", 1)[1] if "." in t else t).replace("_", " ")
            for t in tool_names
        ]
        parts.append(f"- **{display}**: {', '.join(actions)}")

    parts.append("")


def _get_service_tool_domains(state: dict[str, Any]) -> dict[str, list[str]]:
    """
    Get user-configured service tools grouped by domain.

    Only returns tools from the agent's configured toolsets — NOT internal
    utility tools. This means new internal tools (calculator, date_calculator,
    web_search, etc.) are automatically excluded without code changes.

    Sources (in priority order):
    1. state["tools"] — canonical flat list from build_initial_state
    2. state["agent_toolsets"] — raw toolset metadata fallback
    """
    domains: dict[str, list[str]] = {}

    # Primary: state["tools"] — populated by build_initial_state from toolsets
    for tool_name in (state.get("tools") or []):
        if not isinstance(tool_name, str) or "." not in tool_name:
            continue
        domain = tool_name.split(".", 1)[0]
        domains.setdefault(domain, []).append(tool_name)

    if domains:
        return domains

    # Fallback: agent_toolsets metadata from graph DB
    for toolset in (state.get("agent_toolsets") or []):
        if not isinstance(toolset, dict):
            continue
        toolset_name = toolset.get("name", "")
        if not toolset_name:
            continue

        tool_names: list[str] = []

        for t in (toolset.get("tools") or []):
            if isinstance(t, dict):
                name = t.get("fullName") or f"{toolset_name}.{t.get('name', '')}"
                tool_names.append(name)

        if not tool_names:
            for t in (toolset.get("selectedTools") or []):
                if isinstance(t, str):
                    name = t if "." in t else f"{toolset_name}.{t}"
                    tool_names.append(name)

        if tool_names:
            domains.setdefault(toolset_name, []).extend(tool_names)

    return domains