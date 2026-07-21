"""
Internal Knowledge Retrieval Tool

- Writes results directly to state (accumulates for parallel calls)
- Returns properly formatted <record> tool messages (same as chatbot)
- Block numbering (R-labels) happens ONCE after all parallel calls are merged
"""

import json
import logging
from datetime import datetime
from typing import Any

from langgraph.types import StreamWriter
from pydantic import BaseModel, Field

from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import ToolsetBuilder
from app.modules.agents.qna.chat_state import ChatState
from app.modules.transformers.blob_storage import BlobStorage
from app.utils.chat_helpers import (
    CitationRefMapper,
    build_message_content_array,
    get_flattened_results,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms, parse_timestamp

logger = logging.getLogger(__name__)

# Cap the divisor to prevent excessively small per-source limits when many
# knowledge sources are configured simultaneously.
_MAX_RETRIEVAL_SOURCES_DIVISOR = 5

# Small grace (5 minutes) for client/server clock skew when validating that
# created_after is not set to a future timestamp.
_FUTURE_TIMESTAMP_GRACE_MS = 5 * 60 * 1000


def _summarize_records_for_log(search_results: list[dict[str, Any]], limit: int = 20) -> list[dict[str, str]]:
    """Build a compact (recordId, recordName) summary of search results for logging.

    Caps at `limit` entries so a large result set doesn't flood the logs.
    """
    summary = []
    for result in search_results[:limit]:
        meta = result.get("metadata", {}) if isinstance(result, dict) else {}
        summary.append({
            "recordId": meta.get("recordId", ""),
            "recordName": meta.get("recordName", ""),
        })
    if len(search_results) > limit:
        summary.append({"...": f"+{len(search_results) - limit} more"})
    return summary


def _normalize_list_param(value: str | list[str] | None) -> list[str] | None:
    """Normalize a parameter that should be a list of strings.
    Handles LLM sending a single string instead of a list, or empty list."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return [value] if value else None
    if isinstance(value, list):
        filtered = [str(v).strip() for v in value if v]
        return filtered if filtered else None
    return None


def _parse_iso_time_bound(value: str, field_name: str) -> tuple[int | None, str | None]:
    """Parse an ISO 8601 bound to epoch ms. Returns (epoch_ms, error_json)."""
    value = value.strip()
    if not value:
        return None, None
    try:
        iso = value
        if iso.endswith("Z") or iso.endswith("z"):
            iso = iso[:-1] + "+00:00"
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            return None, json.dumps({
                "status": "error",
                "message": (
                    f"Invalid ISO 8601 timestamp for {field_name}: include a timezone offset "
                    "(e.g. -07:00 or Z)."
                ),
            })
        return parse_timestamp(value), None
    except (ValueError, TypeError):
        return None, json.dumps({
            "status": "error",
            "message": (
                f"Invalid ISO 8601 timestamp for {field_name}: {value!r}. "
                "Use ISO 8601 with timezone offset."
            ),
        })


def _build_time_range_from_iso(
    created_after: str | None,
    created_before: str | None,
    updated_after: str | None = None,
    updated_before: str | None = None,
) -> tuple[dict[str, int] | None, str | None]:
    """Build time_range dict from optional ISO bounds. Returns (time_range, error_json).

    Keys in the returned dict:
      source_created_after_ms / source_created_before_ms  → filters on sourceCreatedAtTimestamp
      source_updated_after_ms / source_updated_before_ms  → filters on sourceLastModifiedTimestamp
    """
    time_range: dict[str, int] = {}

    if created_after:
        after_ms, err = _parse_iso_time_bound(created_after, "created_after")
        if err:
            return None, err
        if after_ms is not None:
            time_range["source_created_after_ms"] = after_ms

    if created_before:
        before_ms, err = _parse_iso_time_bound(created_before, "created_before")
        if err:
            return None, err
        if before_ms is not None:
            time_range["source_created_before_ms"] = before_ms

    if updated_after:
        after_ms, err = _parse_iso_time_bound(updated_after, "updated_after")
        if err:
            return None, err
        if after_ms is not None:
            time_range["source_updated_after_ms"] = after_ms

    if updated_before:
        before_ms, err = _parse_iso_time_bound(updated_before, "updated_before")
        if err:
            return None, err
        if before_ms is not None:
            time_range["source_updated_before_ms"] = before_ms

    if (
        "source_created_after_ms" in time_range
        and "source_created_before_ms" in time_range
        and time_range["source_created_after_ms"] > time_range["source_created_before_ms"]
    ):
        return None, json.dumps({
            "status": "error",
            "message": (
                "created_after must be on or before created_before. "
                f"Got created_after={created_after!r}, created_before={created_before!r}."
            ),
        })

    if (
        "source_updated_after_ms" in time_range
        and "source_updated_before_ms" in time_range
        and time_range["source_updated_after_ms"] > time_range["source_updated_before_ms"]
    ):
        return None, json.dumps({
            "status": "error",
            "message": (
                "updated_after must be on or before updated_before. "
                f"Got updated_after={updated_after!r}, updated_before={updated_before!r}."
            ),
        })

    # Guard: created_after must not be in the future — no document can be ingested
    # in the future, so a future lower bound on creation time returns zero results.
    now_ms = get_epoch_timestamp_in_ms()
    c_after_ms = time_range.get("source_created_after_ms")
    if c_after_ms is not None and c_after_ms > now_ms + _FUTURE_TIMESTAMP_GRACE_MS:
        return None, json.dumps({
            "status": "error",
            "message": (
                f"created_after={created_after!r} is in the future. This filter is the "
                "document's ingestion time and must not be a future date. For event-time "
                "queries (e.g. 'scheduled for next week', 'will happen', 'upcoming'), the "
                "planning document was created BEFORE the event — retry with created_after "
                "set to a planning lead time before today (typically ~4 weeks for near-term "
                "events, ~12 months for yearly horizons) and leave created_before null. If "
                "the planning horizon is genuinely unknowable, omit both bounds."
            ),
        })

    return (time_range if time_range else None), None


class RetrievalToolOutput(BaseModel):
    """Structured output from the retrieval tool."""
    status: str = Field(default="success", description="Status: 'success' or 'error'")
    content: str = Field(description="Formatted content for LLM consumption")
    final_results: list[dict[str, Any]] = Field(description="Processed results for citation generation")
    virtual_record_id_to_result: dict[str, dict[str, Any]] = Field(description="Mapping for citation normalization")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class _EntityFilterDecision(BaseModel):
    """Structured output from the entity filter classification LLM call."""
    selected_entities: list[str] = Field(
        default_factory=list,
        description=(
            "Names of candidate entities that should be applied as search filters. "
            "Empty list if none are genuine filtering facets."
        ),
    )
    reasoning: str = Field(
        default="",
        description="One-sentence explanation of the selection decision.",
    )


_ENTITY_FILTER_SYSTEM_PROMPT = """\
You are an entity filter classifier for an enterprise search system.

Given a user query and a list of candidate knowledge-graph entities, decide which
entities (if any) should be applied as SEARCH FILTERS to narrow the document universe.

## Entity Types

- category / subcategory / topic / department / person: topical facets. Documents are
  TAGGED with these — a filter narrows results to documents sharing the tag.
- record: the exact name of a specific document/file/message. A record match means the
  user is asking about (or naming) ONE specific document — selecting it filters the
  search to that exact document by name.
- record_group: the name of a folder, drive, space, project, or other container of
  records. A record_group match means the user is scoping the query to everything
  inside that container.

## Decision Rule

Select an entity as a filter when it is **topically relevant** to the query. Entities
represent organizational categories, topics, departments, and people — documents are
tagged with them in the knowledge graph. Applying a relevant entity as a filter narrows
results to documents that are actually tagged with that entity, improving precision.

The system has a built-in fallback: if filters produce zero results (e.g. documents are
not yet tagged), it automatically retries without filters. So prefer to apply relevant
filters — the fallback handles misses safely.

## Combination Constraint (IMPORTANT)

Filter types (categories, topics, departments, people, records, record_groups) are
combined with AND semantics — a document must match EVERY selected type
simultaneously. Selecting entities across multiple different types drastically
shrinks the result set, often to zero, even when each type alone would have
matched plenty of documents.

- record and record_group candidates identify a specific document or container by
  exact name. If the query is clearly naming a specific document or folder ("find
  the Q3 Security Compliance Questionnaire", "search inside the Marketing Assets
  folder"), select ONLY that record/record_group candidate and skip category,
  topic, department, and person candidates for this call — combining an exact
  name match with a topical facet is redundant and risks a zero-result AND.

- Prefer selecting entities from a SINGLE filter type per query. Pick the type
  most likely to scope the query correctly (department/category are usually
  safer/narrower than topic).
- Do NOT combine a topic with a department or category in the same selection.
  Topics are best explored independently — if a topic candidate is relevant,
  select it ALONE and skip any department/category candidates for this call,
  rather than stacking them together.
- Only select entities from more than one type when you are highly confident
  the intersection is what the user wants (e.g. the query explicitly names
  both a department AND a distinct category, like "HR contracts").
- When uncertain, select fewer entities (or none) rather than more — a missed
  filter degrades gracefully via the zero-result fallback; an over-constrained
  AND across types can silently return nothing.

## When to SELECT an entity as a filter:

- The entity name matches or is closely related to the query topic.
  "Find Security Compliance Questionnaire" + candidate "Security Compliance Questionnaire"
  (category) → SELECT. Documents tagged with this category are exactly what the user wants.
- The query mentions a department, team, or person.
  "engineering team docs" + "Engineering" (department) → SELECT.
  "what did Alice work on?" + "Alice Johnson" (person) → SELECT.
- The entity represents a broader or narrower scope of what the user is asking about.
  "OAuth setup guide" + "Authentication" (category) → SELECT (parent category is relevant).
  "HR policies" + "Human Resources" (department) → SELECT.
- The query names a specific document or container that matches a record/record_group
  candidate closely.
  "find the Vendor Onboarding Checklist" + "Vendor Onboarding Checklist" (record) →
  SELECT (alone — this is an exact document lookup).
  "what's in the Legal Contracts folder?" + "Legal Contracts" (record_group) → SELECT.

## When to NOT select:

- The entity is completely unrelated to the query — it appeared as a candidate only due
  to superficial embedding similarity.
  "quarterly revenue report" + "Security Compliance" (category) → SKIP (unrelated).
- The entity is too generic to be useful as a filter.
  "Tell me about everything" + "General" (category) → SKIP.
- The query is a casual greeting or non-informational.
  "hello" → return empty list.

## Output

Return a JSON object with:
- selected_entities: list of entity names to use as filters (subset of candidates,
  or empty list). Respect the Combination Constraint above — prefer entities from
  a single filter type; do not mix topics with departments/categories.
- reasoning: one sentence explaining the decision
"""

_ENTITY_FILTER_CANDIDATE_TEMPLATE = """\
User query: {query}

Candidate entities from the knowledge graph:
{candidates}

Which of these candidates (if any) should be used as search filters?
Respond with a JSON object matching the required schema.
"""


async def _resolve_entity_filters_with_llm(
    query: str,
    candidates: list[dict[str, Any]],
    llm: Any,
    log: logging.Logger,
) -> dict[str, list[str]]:
    """Use an LLM to decide which candidate entities are genuine search filters.

    Returns a dict with keys 'categories', 'topics', 'departments', 'people',
    'records', 'record_groups' containing entity names selected by the LLM.
    Any key with an empty list is omitted from the result.

    Fails open — returns an empty dict on any error so the caller proceeds
    without entity filters rather than blocking the search.
    """
    if not candidates or not query.strip():
        return {}

    candidate_lines = "\n".join(
        f"- name={c.get('name', '')!r}  type={c.get('entityType', '')}"
        for c in candidates
    )
    human_content = _ENTITY_FILTER_CANDIDATE_TEMPLATE.format(
        query=query,
        candidates=candidate_lines,
    )

    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        structured_llm = llm.with_structured_output(_EntityFilterDecision)
        decision: _EntityFilterDecision = await structured_llm.ainvoke(
            [
                SystemMessage(content=_ENTITY_FILTER_SYSTEM_PROMPT),
                HumanMessage(content=human_content),
            ]
        )
        selected = set(decision.selected_entities or [])
        log.info(
            "LLM entity filter decision | selected=%s | reasoning=%r",
            list(selected),
            decision.reasoning,
        )
    except Exception as exc:
        log.debug("LLM entity filter classification failed (fail-open): %s", exc)
        return {}

    if not selected:
        return {}

    # Build a name→hit map so we can group by entity type
    name_to_hit = {c.get("name", ""): c for c in candidates}
    result: dict[str, list[str]] = {}
    for name in selected:
        hit = name_to_hit.get(name)
        if not hit:
            continue
        etype = hit.get("entityType", "")
        if etype in ("category", "subcategory"):
            result.setdefault("categories", []).append(name)
        elif etype == "topic":
            result.setdefault("topics", []).append(name)
        elif etype == "department":
            result.setdefault("departments", []).append(name)
        elif etype == "person":
            result.setdefault("people", []).append(name)
        elif etype == "record":
            result.setdefault("records", []).append(name)
        elif etype == "record_group":
            result.setdefault("record_groups", []).append(name)
    return result


class ResolveEntityFiltersInput(BaseModel):
    """Input schema for the resolve_entity_filters tool."""

    query_facets: list[str] = Field(
        description=(
            "Natural-language facets to resolve into entity IDs. Each item is a "
            "distinct aspect of the query that could map to a category, topic, "
            "department, person, record, or record group.  E.g. ['engineering', "
            "'OAuth', 'Alice Johnson', 'Vendor Onboarding Checklist']."
        )
    )
    entity_types: list[str] | None = Field(
        default=None,
        description=(
            "Optional list to restrict resolution to specific entity types. "
            "Allowed values: category, subcategory, topic, department, person, "
            "record, record_group, connector.  If omitted, all entity types are "
            "searched."
        ),
    )
    top_k: int | None = Field(
        default=5,
        description="Maximum number of entity matches to return per facet (default 5).",
    )


class SearchInternalKnowledgeInput(BaseModel):
    """Input schema for the search_internal_knowledge tool"""
    query: str = Field(description="The search query to find relevant information")
    connector_ids: list[str] | None = Field(default=None, description="Filter to specific connectors by their IDs. If not provided or IDs don't match agent scope, uses all agent connectors.")
    collection_ids: list[str] | None = Field(default=None, description="Filter to specific KB collections by their record group IDs. If not provided or IDs don't match agent scope, uses all agent collections.")
    category_ids: list[str] | None = Field(
        default=None,
        description=(
            "Optional list of category/subcategory entity NAMES from resolve_entity_filters. "
            "Narrows the search to documents tagged with these categories. "
            "Pass the 'name' field (not entityId) for matches with score > 0.5."
        ),
    )
    topic_ids: list[str] | None = Field(
        default=None,
        description=(
            "Optional list of topic entity NAMES from resolve_entity_filters. "
            "Narrows the search to documents tagged with these topics. "
            "Pass the 'name' field (not entityId) for matches with score > 0.5."
        ),
    )
    department_ids: list[str] | None = Field(
        default=None,
        description=(
            "Optional list of department entity NAMES from resolve_entity_filters. "
            "Narrows the search to documents from the specified departments. "
            "Pass the 'name' field (not entityId) for matches with score > 0.5."
        ),
    )
    people_ids: list[str] | None = Field(
        default=None,
        description=(
            "Optional list of person entity NAMES from resolve_entity_filters. "
            "Narrows the search to documents associated with the specified people. "
            "Pass the 'name' field (not entityId) for matches with score > 0.5."
        ),
    )
    record_names: list[str] | None = Field(
        default=None,
        description=(
            "Optional list of exact document/record NAMES from resolve_entity_filters. "
            "Use when the query names a specific document — narrows the search to "
            "that exact record. Pass the 'name' field (not entityId)."
        ),
    )
    record_group_names: list[str] | None = Field(
        default=None,
        description=(
            "Optional list of record group (folder/drive/space/project) NAMES from "
            "resolve_entity_filters. Narrows the search to documents inside the "
            "specified group(s). Pass the 'name' field (not entityId)."
        ),
    )
    created_after: str | None = Field(
        default=None,
        description=(
            "Optional inclusive lower bound on the document's INGESTION time in the source "
            "system. ISO 8601 with timezone offset, e.g. '2026-05-14T00:00:00-07:00'. Resolve "
            "relative dates against the **Current time** and **Time zone** in your system "
            "prompt — never invent a date. Must never be in the future (no document can be "
            "ingested in the future).\n\n"
            "Two ways to use:\n\n"
            "1. DOCUMENT-time queries — the time word modifies the document itself: "
            "'docs created last week', 'emails I received in May', 'files uploaded since "
            "Monday'. Set created_after / created_before to the exact window the user gave.\n\n"
            "2. EVENT-time queries — the time word refers to an event, plan, deployment, or "
            "milestone DESCRIBED IN the document. The planning / announcement document for "
            "that event was almost always created BEFORE the event, so back off created_after "
            "by a reasonable planning lead time:\n"
            "  - Near-term events (next week / this week / tomorrow): set created_after to "
            "~4 weeks before today; leave created_before null.\n"
            "  - This month / next month: ~2-3 months before today; leave created_before null.\n"
            "  - This quarter / next quarter: ~6 months before today.\n"
            "  - This year / last year / longer-range: ~12 months before today, or skip the "
            "filter entirely if the planning horizon could be multi-year.\n"
            "  For past events that have already happened, you may also set created_before to "
            "the event date plus a short tail (~2 weeks) to exclude post-mortem rewrites.\n\n"
            "Examples (assume today is 2026-05-21):\n"
            "  - 'ECOs scheduled for deployment next week' → created_after='2026-04-23' "
            "(~4 weeks back), created_before=null.\n"
            "  - 'Which ECOs will cause downtime next week?' → created_after='2026-04-23', "
            "created_before=null.\n"
            "  - 'How many ECOs were deployed this year?' → created_after='2025-05-21' "
            "(~12 months back), created_before=null.\n"
            "  - 'Docs I created last week' → created_after='2026-05-14', "
            "created_before='2026-05-21' (exact window).\n\n"
            "If the planning horizon is genuinely unknowable, OMIT both bounds and let "
            "semantic search find the document on its own."
        ),
    )
    created_before: str | None = Field(
        default=None,
        description=(
            "Optional inclusive upper bound on the document's INGESTION time in the source "
            "system. Same format as created_after. For DOCUMENT-time queries, set to the "
            "exact upper edge of the user's window. For EVENT-time queries, usually leave "
            "null (planning docs may still be updated); for past events, you may cap it at "
            "the event date plus a short tail."
        ),
    )
    updated_after: str | None = Field(
        default=None,
        description=(
            "Optional inclusive lower bound on the document's LAST MODIFICATION time in the "
            "source system (when the file/page/ticket was last edited or updated). ISO 8601 "
            "with timezone offset, e.g. '2026-05-14T00:00:00-07:00'.\n\n"
            "USE for queries about documents that were recently modified, regardless of when "
            "they were first created: 'pages updated last week', 'files edited in May', "
            "'tickets modified since Monday', 'Confluence pages changed this month'.\n\n"
            "A page created a year ago but edited last week has a creation timestamp from a "
            "year ago — using created_after would miss it entirely. Use updated_after instead.\n\n"
            "DO NOT use for queries where 'update' refers to an event in content (e.g. "
            "'status update on the Q4 launch' — that is a semantic search, not a time filter)."
        ),
    )
    updated_before: str | None = Field(
        default=None,
        description=(
            "Optional inclusive upper bound on the document's LAST MODIFICATION time. "
            "Same format as updated_after. Usually omitted for 'since / after' queries; "
            "set to close the window for 'between X and Y' queries."
        ),
    )


@ToolsetBuilder("Retrieval")\
    .in_group("Internal Tools")\
    .with_description("Internal knowledge retrieval tool - always available, no authentication required")\
    .with_category(ToolCategory.UTILITY)\
    .with_auth([
        AuthBuilder.type("NONE").fields([])
    ])\
    .as_internal()\
    .configure(lambda builder: builder.with_icon("/assets/icons/toolsets/retrieval.svg"))\
    .build_decorator()

class Retrieval:
    """Internal knowledge retrieval tool exposed to agents"""

    def __init__(self, state: ChatState | None = None, writer: StreamWriter | None = None, **kwargs) -> None:
        self.state: ChatState | None = state or kwargs.get('state')
        self.writer = writer
        logger.info("🚀 Initializing Internal Knowledge Retrieval tool")

    @tool(
        app_name="retrieval",
        tool_name="search_internal_knowledge",
        description=(
            "Search and retrieve information from internal collections and indexed applications"
        ),
        args_schema=SearchInternalKnowledgeInput,
        llm_description=(
            "Search and retrieve information from indexed company documents, knowledge "
            "bases, and connected data sources. Returns content chunks with citations.\n\n"
            "ENTITY FILTERING (automatic or via resolve_entity_filters):\n"
            "Entity filtering is applied automatically when the query matches known "
            "knowledge-graph entities. You can also call resolve_entity_filters first "
            "and pass entity NAMES (not entityId) via category_ids, topic_ids, "
            "department_ids, people_ids, record_names, or record_group_names for "
            "explicit control. record_names/record_group_names narrow to an exact "
            "document or folder/space by name — use alone, not combined with "
            "category/topic/department filters. "
            "If filtered search returns 0 results, retry WITHOUT entity filters.\n\n"
            "HYBRID-SEARCH RULE: when the agent has BOTH this tool AND a search tool for "
            "an indexed service (e.g. Confluence, Jira, Drive, OneDrive, etc.) available, call "
            "BOTH in PARALLEL for any topic / information query. Indexed snapshots and "
            "live API data complement each other — the user gets a richer answer when "
            "both are merged. Some service tools are live-only (e.g. Slack, Outlook, "
            "Gmail, Calendar) — for those, follow the planner's per-service rules instead "
            "of pairing with retrieval. Only skip this tool entirely for: exact ID "
            "lookups (use the service tool), write actions, real-time-only data ('my "
            "unread mail right now'), pure greetings, or arithmetic.\n\n"
            "TIME-RANGE — choose the right pair of bounds:\n"
            "- 'pages updated last week', 'files edited in May', 'tickets changed since "
            "Monday' → use updated_after / updated_before (last-modification time). A doc "
            "created a year ago but edited last week will be MISSED if you use created_after.\n"
            "- 'docs created last week', 'emails I received in May' → use created_after / "
            "created_before (original ingestion time).\n"
            "- Event-time queries ('scheduled for next week', 'deployed this year'): the "
            "planning doc was created BEFORE the event — back created_after off by a lead "
            "time (~4 weeks for near-term, ~12 months for yearly) and leave created_before "
            "null. See the created_after schema for examples.\n"
            "- NEVER set created_after to a future timestamp; the server will reject it.\n"
            "Resolve relative dates from the **Current time** and **Time zone** in your "
            "system prompt."
        ),
        category=ToolCategory.KNOWLEDGE,
        is_essential=True,
        requires_auth=False,
        when_to_use=[
            "Any topic, keyword, concept, name, or phrase — even a single bare word",
            "Information / documentation requests ('what is X', 'how does Y work', 'tell me about Z')",
            "Policy / procedure / general knowledge questions",
            "ALWAYS in parallel with a service search tool when one is configured for the same topic",
            "When the query asks about a person, entity, or topic that is NOT present in the attached documents** — do NOT refuse; search the internal knowledge base instead.",
            "Modification-time queries ('pages updated last week', 'files edited in May', 'tickets changed since Monday'): use updated_after / updated_before — NOT created_after, which would miss docs created before the window.",
            "Document-creation-time queries ('docs created last week', 'emails I received in May'): use created_after / created_before.",
            "Event-time queries ('scheduled for next week', 'will be deployed', 'deployed this year'): use created_after set to a planning lead time before today (~4 weeks for near-term, ~12 months for yearly), leave created_before null. NEVER set created_after to a future date.",
            "After resolve_entity_filters: pass entity names (not IDs) via category_ids, topic_ids, department_ids, people_ids, record_names, record_group_names to narrow the search universe.",
        ],
        when_not_to_use=[
            "Exact ID lookup ('get page 12345') — use the service tool directly",
            "Write actions (create / update / delete) — use the service tool",
            "Real-time-only data ('my unread mail right now', 'today's calendar') — use the service tool",
            "Pure greetings, thanks, or arithmetic",
            "ONLY when the attachment content fully and directly answers the query for the **exact same** person, entity, or topic being asked about — do not call this tool unnecessarily.",
            "Omit created_after / created_before only when the planning horizon is genuinely unknowable (e.g. multi-year roadmap with no anchor date)."
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "What is our vacation policy?",
            "How do I submit expenses?",
            "Find information about Q4 results"
        ]
    )
    async def search_internal_knowledge(
        self,
        query: str | None = None,
        connector_ids: list[str] | None = None,
        collection_ids: list[str] | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        updated_after: str | None = None,
        updated_before: str | None = None,
        category_ids: list[str] | None = None,
        topic_ids: list[str] | None = None,
        department_ids: list[str] | None = None,
        people_ids: list[str] | None = None,
        record_names: list[str] | None = None,
        record_group_names: list[str] | None = None,
    ) -> str:
        """Search internal knowledge bases and return formatted results."""
        search_query = query

        if not search_query:
            return json.dumps({
                "status": "error",
                "message": "No search query provided (expected 'query' or 'text' parameter)"
            })

        if not self.state:
            return json.dumps({
                "status": "error",
                "message": "Retrieval tool state not initialized"
            })

        try:
            logger_instance = self.state.get("logger", logger)
            logger_instance.info(
                "🔍 search_internal_knowledge called | "
                "query=%r | connector_ids=%r | collection_ids=%r | "
                "created_after=%r | created_before=%r | "
                "updated_after=%r | updated_before=%r",
                search_query[:200],
                connector_ids,
                collection_ids,
                created_after,
                created_before,
                updated_after,
                updated_before,
            )

            retrieval_service = self.state.get("retrieval_service")
            graph_provider = self.state.get("graph_provider")
            config_service = self.state.get("config_service")

            if not retrieval_service or not graph_provider:
                return json.dumps({
                    "status": "error",
                    "message": "Retrieval services not available"
                })

            org_id = self.state.get("org_id", "")
            user_id = self.state.get("user_id", "")

            # Normalize list inputs
            connector_ids = _normalize_list_param(connector_ids)
            collection_ids = _normalize_list_param(collection_ids)

            time_range, time_range_error = _build_time_range_from_iso(
                created_after, created_before, updated_after, updated_before
            )
            if time_range_error:
                logger_instance.warning(
                    "search_internal_knowledge time-range rejected | "
                    "created_after=%r | created_before=%r | "
                    "updated_after=%r | updated_before=%r | error=%s",
                    created_after,
                    created_before,
                    updated_after,
                    updated_before,
                    time_range_error,
                )
                return time_range_error
            logger_instance.info(
                "search_internal_knowledge time_range resolved | %s",
                time_range if time_range else "no time filter",
            )

            # === BUILD FILTERS — always scoped to agent's configured knowledge ===
            # Get agent's configured filters from state
            agent_filters = self.state.get("filters", {}) or {}
            agent_filter_apps = set(agent_filters.get("apps") or [])
            agent_filter_kbs = set(agent_filters.get("kb") or [])

            agent_configured_apps = self.state.get("apps", [])
            agent_configured_kbs = self.state.get("kb", [])

            # Start from an empty filter dict — we build it precisely below.
            filter_groups: dict[str, list[str]] = {}

            # === TARGETED vs BROAD FILTER LOGIC ===
            #
            # Rule: if the caller explicitly provides EITHER connector_ids OR
            # collection_ids, treat that as a targeted search and do NOT add the
            # other side from the agent scope. Mixing both would create an
            # unnecessary union that defeats the purpose of the explicit filter.
            #
            # Only when NEITHER is provided do we fall back to the full agent
            # scope (both connectors and KB collections).
            #
            explicit_connectors = bool(connector_ids)
            explicit_collections = bool(collection_ids)
            broad_search = not explicit_connectors and not explicit_collections

            # Placeholder agent: broaden scope to all configured connectors/KBs
            # since filters are not author-curated for this synthetic agent.
            is_placeholder_agent = self.state.get("is_placeholder_agent", False)
            if is_placeholder_agent:
                agent_filter_apps = list(agent_configured_apps) if agent_configured_apps else []
                agent_filter_kbs = list(agent_configured_kbs) if agent_configured_kbs else []

            agent_connector_ids_count = len(agent_filter_apps)
            agent_collection_ids_count = len(agent_filter_kbs)
            total_sources = agent_connector_ids_count + agent_collection_ids_count
            if total_sources <= 1:
                adjusted_limit = 50
            else:
                adjusted_limit = 100 // min(total_sources, _MAX_RETRIEVAL_SOURCES_DIVISOR)

            logger_instance.debug(f"is_placeholder_agent: {is_placeholder_agent}")
            logger_instance.debug(f"agent_filter_apps: {sorted(agent_filter_apps)}")
            logger_instance.debug(f"agent_filter_kbs: {sorted(agent_filter_kbs)}")

            # --- App connectors ---
            if explicit_connectors:
                # Scope to the intersection with the agent's allowed connectors.
                resolved_apps = [cid for cid in connector_ids if cid in agent_filter_apps]
                # If the LLM hallucinated an ID not in scope, ignore it and use
                # the full agent connector set as a safe fallback.
                filter_groups["apps"] = resolved_apps if resolved_apps else list(agent_filter_apps)
            elif broad_search:
                # No explicit filter — include all agent connectors.
                filter_groups["apps"] = list(agent_filter_apps) if agent_filter_apps else []
            else:
                # collection_ids were given but connector_ids were not:
                # exclude connectors entirely so the search is KB-only.
                filter_groups["apps"] = []

            # --- KB collections ---
            if explicit_collections:
                # Scope to the intersection with the agent's allowed KB groups.
                resolved_kbs = [cid for cid in collection_ids if cid in agent_filter_kbs]
                # Fallback to full KB scope if IDs don't match.
                filter_groups["kb"] = resolved_kbs if resolved_kbs else list(agent_filter_kbs)
            elif broad_search:
                # No explicit filter — include all agent KB collections.
                filter_groups["kb"] = list(agent_filter_kbs) if agent_filter_kbs else []
            else:
                # connector_ids were given but collection_ids were not:
                # exclude KB collections so the search is connector-only.
                filter_groups["kb"] = ['NO_KB_SELECTED']
                if is_placeholder_agent:
                    filter_groups["kb"] = []

            # --- Entity filters (from resolve_entity_filters or auto-resolution) ---
            norm_category_ids = _normalize_list_param(category_ids)
            norm_topic_ids = _normalize_list_param(topic_ids)
            norm_department_ids = _normalize_list_param(department_ids)
            norm_people_ids = _normalize_list_param(people_ids)
            norm_record_names = _normalize_list_param(record_names)
            norm_record_group_names = _normalize_list_param(record_group_names)

            has_explicit_entity_filters = any([
                norm_category_ids, norm_topic_ids, norm_department_ids, norm_people_ids,
                norm_record_names, norm_record_group_names,
            ])

            # Auto-resolve entities when no explicit filters were passed.
            # Step 1: pull candidate entities from the vector store (no score gate —
            #         let the LLM make the relevance call).
            # Step 2: ask the LLM which candidates are genuine *filtering* facets
            #         vs. the search target itself.
            # We use entity *names* (not graph DB IDs) because the graph
            # providers filter on human-readable names in Cypher/AQL queries.
            if not has_explicit_entity_filters:
                entity_vs = self.state.get("entity_vector_store")
                if entity_vs and search_query:
                    try:
                        candidates = await entity_vs.search_entities(
                            query=search_query,
                            org_id=org_id,
                            top_k=10,
                            score_threshold=0.0,
                        )
                        if candidates:
                            # Prefer the cheap indexing-role model; fall back to
                            # the agent LLM if config_service is unavailable.
                            classifier_llm = None
                            config_service = self.state.get("config_service")
                            if config_service:
                                try:
                                    from app.utils.llm import get_llm_for_role
                                    classifier_llm, _ = await get_llm_for_role(
                                        config_service, "indexing"
                                    )
                                except Exception:
                                    pass
                            if classifier_llm is None:
                                classifier_llm = self.state.get("llm")

                            if classifier_llm is not None:
                                resolved = await _resolve_entity_filters_with_llm(
                                    query=search_query,
                                    candidates=candidates,
                                    llm=classifier_llm,
                                    log=logger_instance,
                                )
                                if resolved.get("categories"):
                                    norm_category_ids = resolved["categories"]
                                if resolved.get("topics"):
                                    norm_topic_ids = resolved["topics"]
                                if resolved.get("departments"):
                                    norm_department_ids = resolved["departments"]
                                if resolved.get("people"):
                                    norm_people_ids = resolved["people"]
                                if resolved.get("records"):
                                    norm_record_names = resolved["records"]
                                if resolved.get("record_groups"):
                                    norm_record_group_names = resolved["record_groups"]

                                if any([norm_category_ids, norm_topic_ids,
                                        norm_department_ids, norm_people_ids,
                                        norm_record_names, norm_record_group_names]):
                                    logger_instance.info(
                                        "LLM-resolved entity filters | "
                                        "categories=%s topics=%s departments=%s people=%s "
                                        "records=%s record_groups=%s",
                                        norm_category_ids or None,
                                        norm_topic_ids or None,
                                        norm_department_ids or None,
                                        norm_people_ids or None,
                                        norm_record_names or None,
                                        norm_record_group_names or None,
                                    )
                    except Exception as exc:
                        logger_instance.debug(
                            "Auto entity resolution skipped (non-fatal): %s", exc
                        )

            if norm_category_ids:
                filter_groups["categories"] = norm_category_ids
                logger_instance.info(
                    "Entity filter active: categories=%s", norm_category_ids
                )
            if norm_topic_ids:
                filter_groups["topics"] = norm_topic_ids
                logger_instance.info(
                    "Entity filter active: topics=%s", norm_topic_ids
                )
            if norm_department_ids:
                filter_groups["departments"] = norm_department_ids
                logger_instance.info(
                    "Entity filter active: departments=%s", norm_department_ids
                )
            if norm_people_ids:
                filter_groups["people"] = norm_people_ids
                logger_instance.info(
                    "Entity filter active: people=%s", norm_people_ids
                )
            if norm_record_names:
                filter_groups["records"] = norm_record_names
                logger_instance.info(
                    "Entity filter active: records=%s", norm_record_names
                )
            if norm_record_group_names:
                filter_groups["record_groups"] = norm_record_group_names
                logger_instance.info(
                    "Entity filter active: record_groups=%s", norm_record_group_names
                )

            # === SEARCH ===
            is_service_account = bool(self.state.get("is_service_account", False))
            logger_instance.debug(
                f"Executing retrieval with limit: {adjusted_limit} "
                f"(service_account={is_service_account})"
            )

            logger_instance.debug(f"filter_groups: {filter_groups}")

            logger_instance.debug(f"Executing retrieval with limit: {adjusted_limit}")
            results = await retrieval_service.search_with_filters(
                queries=[search_query],
                org_id=org_id,
                user_id=user_id,
                limit=adjusted_limit,
                filter_groups=filter_groups,
                time_range=time_range,
            )

            if results is None:
                logger_instance.warning("Retrieval service returned None")
                return json.dumps({
                    "status": "error",
                    "message": "Retrieval service returned no results"
                })

            status_code = results.get("status_code", 200)
            if status_code in [202, 500, 503]:
                return json.dumps({
                    "status": "error",
                    "status_code": status_code,
                    "message": results.get("message", "Retrieval service unavailable")
                })

            search_results = results.get("searchResults", [])
            logger_instance.info(f"✅ Retrieved {len(search_results)} documents")

            _ENTITY_FILTER_KEYS = (
                "categories", "topics", "departments", "people", "records", "record_groups",
            )
            entity_filters_active = any(k in filter_groups for k in _ENTITY_FILTER_KEYS)
            if entity_filters_active and search_results:
                logger_instance.info(
                    "📄 Records retrieved via entity filters %s: %s",
                    {k: filter_groups[k] for k in _ENTITY_FILTER_KEYS if k in filter_groups},
                    _summarize_records_for_log(search_results),
                )

            # Fallback: if auto-resolved entity filters caused zero results,
            # retry without those filters (only when there was no explicit
            # entity filter from the agent, i.e. pure auto-resolution).
            auto_resolved_filters = (
                not has_explicit_entity_filters
                and any(k in filter_groups for k in _ENTITY_FILTER_KEYS)
            )
            if not search_results and auto_resolved_filters:
                logger_instance.info(
                    "⚠️ Auto-resolved entity filters yielded 0 results — "
                    "retrying without entity filters"
                )
                for k in _ENTITY_FILTER_KEYS:
                    filter_groups.pop(k, None)
                results = await retrieval_service.search_with_filters(
                    queries=[search_query],
                    org_id=org_id,
                    user_id=user_id,
                    limit=adjusted_limit,
                    filter_groups=filter_groups,
                    time_range=time_range,
                )
                if results:
                    search_results = results.get("searchResults", [])
                    logger_instance.info(
                        f"✅ Retry without entity filters: {len(search_results)} documents"
                    )

            if not search_results:
                return json.dumps({
                    "status": "success",
                    "message": "No results found",
                    "results": [],
                    "result_count": 0
                })

            # === FLATTEN ===

            blob_store = BlobStorage(
                logger=logger_instance,
                config_service=config_service,
                graph_provider=graph_provider
            )

            is_multimodal_llm = False
            try:
                llm_config = self.state.get("llm")
                if hasattr(llm_config, 'model_name'):
                    model_name = str(llm_config.model_name).lower()
                    is_multimodal_llm = any(m in model_name for m in [
                        'gpt-4-vision', 'gpt-4o', 'claude-3', 'gemini-pro-vision'
                    ])
            except Exception:
                pass

            virtual_record_id_to_result = {}
            # Retrieve virtual_to_record_map from search results — same as chatbot.
            # This enriches records with graph-DB metadata (record type, web URL, etc.)
            # so that context_metadata is populated for get_message_content().
            virtual_to_record_map = results.get("virtual_to_record_map", {})

            flattened_results = await get_flattened_results(
                search_results,
                blob_store,
                org_id,
                is_multimodal_llm,
                virtual_record_id_to_result,
                virtual_to_record_map,
                graph_provider=graph_provider,
            )
            logger_instance.info(f"Processed {len(flattened_results)} flattened results")


            final_results = search_results if not flattened_results else flattened_results

            # === TRIM ===
            # Do NOT sort here. The upstream retrieval service returns results
            # ranked by relevance. merge_and_number_retrieval_results() in
            # nodes.py will correctly:
            #   1. Deduplicate blocks across parallel retrieval calls
            #   2. Group blocks by document (by best-score descending)
            #   3. Sort blocks within each document by block_index
            final_results = final_results[:adjusted_limit]

            # ================================================================
            # Write results directly to state (accumulate for parallel calls)
            # and return properly formatted tool message like the chatbot.
            #
            # Block numbering (R-labels) still happens ONCE after all parallel
            # calls are merged in nodes.py (merge_and_number_retrieval_results()).
            # But the ToolMessage content the LLM sees during planning/ReAct
            # is now properly formatted with <record> XML blocks instead of
            # raw JSON dumps.
            # ================================================================

            # --- Accumulate results in state (same pattern as _process_retrieval_output) ---
            existing_final_results = self.state.get("final_results", [])
            if not isinstance(existing_final_results, list):
                existing_final_results = []
            self.state["final_results"] = existing_final_results + final_results

            existing_virtual_map = self.state.get("virtual_record_id_to_result", {})
            if not isinstance(existing_virtual_map, dict):
                existing_virtual_map = {}
            self.state["virtual_record_id_to_result"] = {**existing_virtual_map, **virtual_record_id_to_result}

            existing_tool_records = self.state.get("tool_records", [])
            if not isinstance(existing_tool_records, list):
                existing_tool_records = []
            new_tool_records = list(virtual_record_id_to_result.values())
            existing_record_ids = {r.get("_id") for r in existing_tool_records if isinstance(r, dict) and "_id" in r}
            unique_new = [r for r in new_tool_records if not (isinstance(r, dict) and r.get("_id") in existing_record_ids)]
            self.state["tool_records"] = existing_tool_records + unique_new

            # --- Format results like the chatbot does ---
            sorted_results = sorted(
                final_results,
                key=lambda x: (x.get("virtual_record_id") or "", -1 if x.get("block_index") is None else x.get("block_index"))
            )
            ref_mapper = self.state.get("citation_ref_mapper") or CitationRefMapper()
            message_content_array, ref_mapper = build_message_content_array(
                sorted_results, virtual_record_id_to_result,is_multimodal_llm=is_multimodal_llm, ref_mapper=ref_mapper,from_tool=True
            )
            self.state["citation_ref_mapper"] = ref_mapper

            formatted_records = []
            for content in message_content_array:
                content_string = ""
                for item in content:
                    if item["type"] == "text":
                        content_string += item["text"]
                formatted_records.append(content_string)

            logger_instance.info(
                f"✅ Retrieved {len(final_results)} blocks from "
                f"{len(virtual_record_id_to_result)} documents "
                f"(state updated, formatted as tool message)"
            )

            summary = (
                f"Retrieved {len(final_results)} knowledge blocks from "
                f"{len(virtual_record_id_to_result)} documents.\n\n"
            )
            return summary + "\n".join(formatted_records)

        except Exception as e:
            logger_instance = self.state.get("logger", logger) if self.state else logger
            logger_instance.error(f"Error in retrieval tool: {str(e)}", exc_info=True)
            return json.dumps({
                "status": "error",
                "message": f"Retrieval error: {str(e)}"
            })

    @tool(
        app_name="retrieval",
        tool_name="resolve_entity_filters",
        description=(
            "Resolve natural-language facets to knowledge graph entity IDs for use as "
            "filters in search_internal_knowledge"
        ),
        args_schema=ResolveEntityFiltersInput,
        llm_description=(
            "OPTIONAL OVERRIDE TOOL: search_internal_knowledge already auto-resolves "
            "entities from the query. Only use this tool for explicit fine-grained control "
            "over entity filtering.\n\n"
            "Resolves natural-language terms (e.g. 'engineering', 'OAuth', 'Alice') to "
            "typed knowledge-graph entity names (categories, topics, departments, people, "
            "record groups). Pass the returned 'name' values (NOT entityId) to "
            "search_internal_knowledge via category_ids, topic_ids, department_ids, "
            "or people_ids.\n\n"
            "WHEN TO USE:\n"
            "- You need to disambiguate between multiple entities with similar names\n"
            "- You want to restrict to specific entity types before searching\n"
            "- The auto-resolution in search_internal_knowledge was too broad\n\n"
            "WHEN NOT TO USE:\n"
            "- Broad exploratory queries with no filtering intent ('find anything about X')\n"
            "- When you already have entity IDs from a prior resolve call in this session\n"
            "- When entity_vector_store is unavailable (tool gracefully returns empty)\n\n"
            "SCORE GUIDANCE:\n"
            "  > 0.7  — high confidence, safe to use as a hard filter\n"
            "  0.5–0.7 — medium confidence, use but watch for zero-result fallback\n"
            "  < 0.5  — low confidence, skip and search without entity filters"
        ),
        category=ToolCategory.KNOWLEDGE,
        is_essential=False,
        requires_auth=False,
        when_to_use=[
            "You need to disambiguate between similar entity names before filtering",
            "Auto-resolved entity filters in search_internal_knowledge were too broad or wrong",
            "You want to preview which entities exist before committing to a filter",
        ],
        when_not_to_use=[
            "Normal search queries — search_internal_knowledge auto-resolves entities",
            "Broad queries with no categorical intent",
            "Already have entity names from a prior resolve call in this session",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Find engineering docs about OAuth",
            "What did Alice work on last month?",
            "DevOps incident reports",
            "Security policies from the compliance team",
        ],
    )
    async def resolve_entity_filters(
        self,
        query_facets: list[str] | None = None,
        entity_types: list[str] | None = None,
        top_k: int | None = None,
    ) -> str:
        """Resolve natural-language facets to entity IDs for search filtering."""
        if not query_facets:
            return json.dumps({
                "status": "error",
                "message": "query_facets must be a non-empty list of strings",
            })

        if not self.state:
            return json.dumps({
                "status": "error",
                "message": "Entity resolution tool state not initialized",
            })

        entity_vector_store = self.state.get("entity_vector_store")
        if entity_vector_store is None:
            return json.dumps({
                "status": "success",
                "message": (
                    "Entity resolution is not available (entity collection not initialised). "
                    "Proceed with unfiltered search_internal_knowledge."
                ),
                "resolved": {},
            })

        org_id = self.state.get("org_id", "")
        if not org_id:
            return json.dumps({
                "status": "error",
                "message": "org_id not available in agent state",
            })

        effective_top_k = top_k or 5
        log = self.state.get("logger", logger)
        log.info(
            "resolve_entity_filters | facets=%r | entity_types=%r | top_k=%d",
            query_facets,
            entity_types,
            effective_top_k,
        )

        # Minimum score below which a hit is treated as "no match".
        # Higher than the collection-level _CONFIDENCE_THRESHOLD so the agent
        # only gets confident matches back.
        _AGENT_SCORE_THRESHOLD = 0.35

        resolved: dict[str, list] = {}
        for facet in query_facets:
            facet_str = (facet or "").strip()
            if not facet_str:
                continue
            try:
                hits = await entity_vector_store.search_entities(
                    query=facet_str,
                    org_id=org_id,
                    entity_types=entity_types,
                    top_k=effective_top_k,
                    score_threshold=_AGENT_SCORE_THRESHOLD,
                )
                resolved[facet_str] = hits
            except Exception as exc:
                log.warning("Entity resolution failed for facet %r: %s", facet_str, exc)
                resolved[facet_str] = []

        any_resolved = any(hits for hits in resolved.values())
        return json.dumps({
            "status": "success",
            "resolved": resolved,
            "hint": (
                "Pass the 'name' values (NOT entityId) grouped by entityType to "
                "search_internal_knowledge: "
                "category/subcategory → category_ids, topic → topic_ids, "
                "department → department_ids, person → people_ids, "
                "record → record_names, record_group → record_group_names. "
                "record/record_group matches identify an exact document or folder — "
                "use them alone, not combined with category/topic/department. "
                "Only use matches with score > 0.5. "
                "If filtered search returns 0 results, retry without entity filters."
            ) if any_resolved else (
                "No entities found above confidence threshold. "
                "Proceed with unfiltered search_internal_knowledge."
            ),
        })

