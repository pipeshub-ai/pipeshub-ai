"""``knowledgegraph__find_records``: exact filters, totals and groups over every
record the user may open. See ``docs/record-query.md``.

The output leads with the total and the conditions as applied, so the model
answers "how many" from a number, never by counting rows; a page of rows
follows for citing. Values are never listed up front: ``describe`` returns a
field's top values within the user's permissions when the model needs them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from app.agents.actions.knowledge_graph.listing_citations import cite_listed_records
from app.agents.actions.knowledge_graph.ops.entity_discovery import (
    NAMED_ENTITIES_DISABLED_MSG,
)
from app.agents.actions.knowledge_graph.ops.entity_filters import (
    ENTITY_INDEX_CACHE_KEY,
    limited_scope_note,
    load_entity_access_context,
)
from app.agents.actions.knowledge_graph.ops.scope import search_scope
from app.agents.actions.knowledge_graph.views import CITE_NOTE
from app.agents.agent_loop.knowledge_graph_policy import knowledge_graph_policy_in
from app.agents.agent_loop.protocol.run_usage import llm_usage_reporter
from app.modules.agents.qna.chat_state import remember_record_ids
from app.modules.pipeline.first_use import classify_on_first_use_later
from app.modules.retrieval.entity_permissions import EntityAccessError
from app.modules.retrieval.provenance import graph_record_refs, note_retrieved_by
from app.modules.retrieval.record_fields import (
    FIELDS,
    PERSON_EDGES,
    ROLE_EDGES,
    FieldKind,
    Store,
)
from app.modules.retrieval.record_query import judge as judge_module
from app.modules.retrieval.record_query.engine import (
    QueryNotRunnable,
    row_view,
    run_record_query,
)
from app.modules.retrieval.record_query.evaluate import (
    CLASSIFICATION_FAILED,
    NOT_CLASSIFIED,
)
from app.modules.retrieval.record_query.judge import Candidate, JudgeOutcome, Verdict
from app.modules.retrieval.record_query.model import (
    EntityCondition,
    FieldCondition,
    LinkCondition,
    MentionCondition,
    OrganizationCondition,
    PersonCondition,
    RecordQuery,
    walk_conditions,
)
from app.modules.retrieval.record_query.paging import NEWEST
from app.modules.retrieval.record_query.recall import (
    MAX_POSSIBLE_SHOWN,
    RECALL_TOP_K,
    RecallCheck,
    finish_recall,
    start_search,
)
from app.services.graph_db.interface.graph_db_provider import STRICT_SCOPE_FILTER_KEY
from app.utils.chat_helpers import get_record_id_shortener_if_enabled

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.modules.agents.qna.chat_state import ChatState
    from app.modules.retrieval.entity_permissions import EntityAccessContext
    from app.modules.retrieval.record_query.model import RecordQueryResult

logger = logging.getLogger(__name__)

NOT_ENABLED_MSG = "find_records is not enabled for this request."
LOOKUP_FAILED_MSG = "The record query failed - try again, or narrow it. This is not an empty result."
PEOPLE_NOT_ALLOWED_MSG = (
    "People and organisation conditions are not available in this workspace; filter by the other fields."
)
_TOP_ROWS_SHOWN_AS_IDS = 50
# The where shape of a mentions condition, in the tool description; left out
# where named entities are off.
MENTIONS_CONDITION_TEXT = (
    '{"mentions": {"kind", "name", "amount": {"from", "to", "currency"}, "quantity": {"from", "to", '
    '"dimension"}, "date": {"from", "to"}, "percent": {"from", "to"}}} (a value written in the text however '
    'it is phrased, "deals over $50k"; percent as a fraction; a mention, never a field to total or group), '
)
RECALL_DID_NOT_RUN = (
    "Recall check did not run (search failed or timed out): records the graph could not link are not listed."
)
RECALL_SKIPPED = "Recall check skipped: no condition here is something search can look for."
RECALL_ON_FIRST_PAGE = "Recall check: ran on the first page of this query; its possible matches are listed there."
POSSIBLE_RULE = (
    "Never add them to the exact total: say how many are possible, or read one with fetch to decide it."
)
JUDGE_DID_NOT_RUN = "Judge did not run ({why}): the possible matches below are unjudged."
JUDGED_NOTE = (
    "A model read their text against the conditions: its judgement is not a graph fact, "
    "and the exact total above does not change."
)
LIKELY_HEADER = "Likely matches (model judgement, not counted): {n}"
STILL_POSSIBLE_HEADER = "Still possible (their text does not settle it): {n}"
UNCLEAR_HINT = "To decide one of these, read it with fetch by its record_id."
MENTIONS_NOTE = (
    "mentions matched values written in the records' text (as extracted), not record fields: "
    "count these records, never total or group the mentioned values."
)
# Share of matched plus unclassified records above which the answer is said to be mostly unknown.
_UNKNOWN_DOMINATES = 0.5
# Row facts a citation quotes (row_view names), in this order.
_CITED_FACTS = ("type", "source", "status", "priority", "created", "updated", "resolved", "amount", "outcome",
                "close_date")


def field_guide() -> str:
    """Field names only, for the tool description: values come from ``describe``."""
    by_kind: dict[str, list[str]] = {}
    for f in FIELDS.values():
        if f.store is not Store.EDGE:  # people, organisations and links have their own conditions
            by_kind.setdefault(f.kind.value, []).append(f.name)
    return "; ".join(f"{kind}: {', '.join(names)}" for kind, names in by_kind.items())


def _resolve_ids(query: RecordQuery, state: ChatState) -> None:
    """Ids the model copied from earlier output may be shortened; resolve them in place."""
    shortener = get_record_id_shortener_if_enabled(state)
    if shortener is None:
        return
    for cond in walk_conditions(query.where):
        if isinstance(cond, PersonCondition) and cond.person.ids:
            cond.person.ids = [shortener.resolve(i) for i in cond.person.ids]
        elif isinstance(cond, OrganizationCondition):
            cond.organization.ids = [shortener.resolve(i) for i in cond.organization.ids]
        elif isinstance(cond, LinkCondition):
            cond.linked_to.ids = [shortener.resolve(i) for i in cond.linked_to.ids]
        elif isinstance(cond, EntityCondition):
            cond.entity.id = shortener.resolve(cond.entity.id)


def _errors(exc: ValidationError) -> str:
    seen: list[str] = []
    for err in exc.errors():
        msg = str(err.get("msg", "")).removeprefix("Value error, ")
        if msg not in seen:
            seen.append(msg)
    return "Invalid query: " + "; ".join(seen[:5])


async def execute_find_records(
    state: ChatState, *, sources: list[str] | None = None, **args: Any,  # noqa: ANN401
) -> tuple[bool, str]:
    """``sources`` (not a tool parameter) are app or knowledge-base ids, as
    find_records_by_value took them: apps narrow the connectors read, and
    without an app among them they are containers."""
    policy = knowledge_graph_policy_in(state)
    if not policy.record_query:
        return False, NOT_ENABLED_MSG
    if not state.get("graph_provider"):
        return False, "Graph provider not available."
    raw = {k: v for k, v in args.items() if v is not None}
    try:
        query = RecordQuery.model_validate(raw)
    except ValidationError as exc:
        return False, _errors(exc)
    conditions = walk_conditions(query.where)
    by_people = any(isinstance(c, (PersonCondition, OrganizationCondition)) for c in conditions)
    if (by_people or (query.group_by and (query.group_by.organization or query.group_by.by_person
                                          or query.group_by.person_role))) and not policy.people_and_companies:
        return False, PEOPLE_NOT_ALLOWED_MSG
    for cond in conditions:
        if isinstance(cond, EntityCondition) and not policy.allows(cond.entity.type):
            return False, f"Entity type {cond.entity.type!r} is not available in this workspace."
    if any(isinstance(c, MentionCondition) for c in conditions) and not policy.named_entities:
        return False, NAMED_ENTITIES_DISABLED_MSG
    _resolve_ids(query, state)
    try:
        context = await load_entity_access_context(state)
        refused, people = await resolve_person_names(state, context, query)
        if refused:
            return True, refused
        apps = [s for s in sources or () if s in context.app_ids]
        if sources and not apps:
            query.where.append(FieldCondition.model_validate({"field": "container", "in": list(sources)}))
        qid = uuid.uuid4().hex[:12]
        passages: dict[str, list[str]] = {}
        # Beside the engine, so it adds no wait of its own; a pure describe has nothing to recall.
        recall_task = None if _no_recall(query) else start_search(
            query, _names(state, people), _search(state, passages),
        )
        try:
            result = await run_record_query(state["graph_provider"], context, query, apps=apps or None,
                                            correlation_id=qid)
        except BaseException:
            if recall_task is not None:
                recall_task.cancel()
            raise
    except QueryNotRunnable as exc:
        return False, str(exc)
    except EntityAccessError:
        logger.warning("find_records failed", exc_info=True)
        return False, LOOKUP_FAILED_MSG
    recall = None if _no_recall(query) else await finish_recall(
        state["graph_provider"], context, result, recall_task, correlation_id=qid,
    )
    if result.not_classified and state.get("config_service") is not None:
        # As a search does: records this query met that wait for classification
        # are classified in the background (only with the Labs flag on), so the
        # next query can decide them.
        classify_on_first_use_later(
            {"records": result.not_classified}, context.org_id, state["config_service"], logger,
        )
    keys = [r["record_id"] for r in result.rows if r.get("record_id")]
    if keys:
        remember_record_ids(state, keys, names={r["record_id"]: r.get("title") or "" for r in result.rows})
        note_retrieved_by(state, "entity", graph_record_refs([{"_key": k} for k in keys]))
    shown = (recall.possible if recall else [])[:MAX_POSSIBLE_SHOWN]
    judged = None
    if shown and judge_module.JUDGE_ENABLED:
        try:
            judged = await _judge(state, query, people, shown, passages, qid)
        except Exception as exc:  # the judge is advisory: its failure never fails the call
            logger.warning("record query %s judge: did not run (%s)", qid, type(exc).__name__)
            judged = JudgeOutcome(error=type(exc).__name__)
    if judged is not None and judged.ran:
        shown = [r for r in shown if judged.of(str(r.get("_key"))) is not Verdict.CONTRADICTS]
    # Each row is cited for the facts it shows, as navigate and list_files cite
    # theirs; totals and groups are computed, not quoted, so they get none.
    possible = [row_view(r) for r in shown]
    if possible:
        remember_record_ids(state, [r["record_id"] for r in possible],
                            names={r["record_id"]: r.get("title") or "" for r in possible})
        note_retrieved_by(state, "search", graph_record_refs([{"_key": r["record_id"]} for r in possible]))
    cite_refs = await cite_listed_records(
        state, state["graph_provider"], context.org_id,
        {r["record_id"]: row_facts(r) for r in [*result.rows, *possible] if r.get("record_id")},
    )
    return True, render(query, result, shortener=get_record_id_shortener_if_enabled(state), cite_refs=cite_refs,
                        scope_note=limited_scope_note(context), people=people, recall=recall, possible=possible,
                        judged=judged)


def _no_recall(query: RecordQuery) -> bool:
    """A pure describe has nothing to recall, and a cursor page continues a
    query whose first page already ran the check: searching again would
    cost a call and list the same possible matches again."""
    describe_only = (bool(query.describe) and query.limit == 0 and query.aggregate.op == "count"
                     and query.group_by is None)
    return describe_only or bool(query.cursor)


def _names(state: ChatState, people: Mapping[str, str]) -> dict[str, str]:
    """Display names search can look for: people named back, and entities search_entities returned."""
    names = {asked: label.split(" (", 1)[0] for asked, label in people.items()}
    for entity_id, entity in (state.get(ENTITY_INDEX_CACHE_KEY) or {}).items():
        if isinstance(entity, dict) and entity.get("name"):
            names.setdefault(str(entity_id), str(entity["name"]))
    return names


def _passage_key(record_id: str = "", virtual_id: str = "") -> str:
    return f"vid:{virtual_id}" if virtual_id else record_id


def _search(state: ChatState, passages: dict[str, list[str]] | None = None) -> Any:  # noqa: ANN401
    """The recall check's search: the search tool's retrieval call, within
    the turn's scope and permissions, record ids best first. ``passages``
    collects each hit's text, best first, by record id and by virtual record
    id, for the judge."""
    async def search(text: str, time_range: dict[str, int] | None) -> list[str]:
        retrieval = state.get("retrieval_service")
        if retrieval is None:
            raise RuntimeError("retrieval service not available")
        filters = state.get("filters") or {}
        groups: dict[str, Any] = dict(search_scope(state).to_filter_groups())
        if filters.get(STRICT_SCOPE_FILTER_KEY):
            groups[STRICT_SCOPE_FILTER_KEY] = True
        if filters.get("entityFilters"):
            groups["entityFilters"] = filters["entityFilters"]
        found = await retrieval.search_with_filters(
            queries=[text], org_id=state.get("org_id") or "", user_id=state.get("user_id") or "",
            limit=RECALL_TOP_K, filter_groups=groups, time_range=time_range,
        )
        if not isinstance(found, dict) or found.get("status_code", 200) >= 400:
            raise RuntimeError("search failed")
        by_vid = found.get("virtual_to_record_map") or {}
        hits = [r for r in found.get("searchResults") or [] if isinstance(r, dict)]
        for hit in hits if passages is not None else ():
            meta, content = hit.get("metadata") or {}, hit.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            for key in {_passage_key(str(meta.get("recordId") or "")),
                        _passage_key(virtual_id=str(meta.get("virtualRecordId") or ""))} - {"", "vid:"}:
                passages.setdefault(key, []).append(content)
        ids = [str((r.get("metadata") or {}).get("recordId") or "") for r in hits]
        ids += [str(rec.get("_key") or "") for rec in by_vid.values() if isinstance(rec, dict)]
        return [i for i in dict.fromkeys(ids) if i]

    return search


def _judge_conditions(query: RecordQuery, people: Mapping[str, str], names: Mapping[str, str]) -> str:
    """The conditions as find_records echoes them, with the names of the ids they hold."""
    applied = _applied(query)
    named = [f"{i} is {n}" for i, n in names.items() if i and f'"{i}"' in applied]
    named += [f'person "{asked}" is {label}' for asked, label in people.items()]
    return applied + (" (" + "; ".join(named) + ")" if named else "")


async def _judge(
    state: ChatState, query: RecordQuery, people: Mapping[str, str], shown: list[dict[str, Any]],
    passages: Mapping[str, list[str]], correlation_id: str,
) -> JudgeOutcome:
    """The judge over ``shown``, possible matches the access check admitted:
    only their own passages from the user's search are sent."""
    from app.agent_loop_lib.transport.opik_tracing import build_langchain_opik_callbacks

    candidates = []
    for row in shown:
        key = str(row.get("_key") or "")
        own = passages.get(_passage_key(key)) or passages.get(
            _passage_key(virtual_id=str(row.get("virtualRecordId") or ""))) or []
        known = {k: v for k, v in row_view(row).items() if k != "record_id"}
        candidates.append(Candidate(key, known, judge_module.bounded_passages(own)))

    async def model() -> Any:  # noqa: ANN401
        llm, role = await _judge_model(state)
        logger.info("record query %s judge: model role=%s", correlation_id, role)
        return llm

    try:
        callbacks = build_langchain_opik_callbacks()
    except Exception:  # tracing never blocks the judge
        callbacks = []
    return await judge_module.judge(
        model, _judge_conditions(query, people, _names(state, people)), candidates, correlation_id=correlation_id,
        on_llm_usage=llm_usage_reporter(state, judge_module.USAGE_SOURCE), callbacks=callbacks,
    )


async def _judge_model(state: ChatState) -> tuple[Any, str]:
    """The ``query``-role model at low effort, as search_by_entities' name
    check picks it; the chat's model only when none can be built. Either
    way the call goes out under the identity the route bound for the turn."""
    from app.agents.actions.knowledge_graph.ops.entity_search import (
        VERIFIER_LLM_LOOKUP_SECONDS,
        VERIFIER_LLM_ROLE,
    )
    from app.utils.llm import get_llm_for_role

    config_service = state.get("config_service")
    if config_service is not None:
        try:
            llm, _ = await asyncio.wait_for(
                get_llm_for_role(config_service, VERIFIER_LLM_ROLE, reasoning_effort="low"),
                timeout=VERIFIER_LLM_LOOKUP_SECONDS,
            )
            if llm is not None:
                return llm, VERIFIER_LLM_ROLE
        except Exception as exc:  # the chat's model below keeps the judge running
            logger.info("record query judge: no %s-role model (%s), using the chat's",
                        VERIFIER_LLM_ROLE, type(exc).__name__)
    return state.get("llm"), "chat"


_AGAIN = 'Call knowledgegraph__find_records again with that person\'s email as person.name.'


async def resolve_person_names(
    state: ChatState, context: EntityAccessContext, query: RecordQuery,
) -> tuple[str | None, dict[str, str]]:
    """Each person condition given by name gets the ids of the one person it
    names, resolved as find_records_by_person resolves one (``match_people``):
    ``me``, an email, or a full name. Returns a reply instead of running when
    a name names several people who link to readable records (they are
    listed to choose from, never guessed) or none: a name no one has and one
    whose people the user shares no readable record with get the same
    find_records_by_person no-match reply, so neither is run nor told
    apart. Also returns the label of each person named back, by the name
    asked."""
    from app.agents.actions.knowledge_graph.ops import people as people_op

    graph, labels = state["graph_provider"], {}
    for cond in walk_conditions(query.where):
        if not isinstance(cond, PersonCondition) or cond.person.ids or cond.person.name is None:
            continue
        asked, role = cond.person.name, cond.person.role
        if asked.casefold() in people_op.SELF_NAMES:
            cond.person.ids = ["me"]
            continue
        edges = list(ROLE_EDGES[role]) if role else list(PERSON_EDGES)
        try:
            shown = await people_op.match_people(state, graph, context, asked, edge_types=edges)
        except ValueError as exc:
            return str(exc), {}
        except EntityAccessError:
            raise
        except Exception as exc:
            raise EntityAccessError("person lookup failed") from exc
        if len(shown) > 1:
            return people_op._ambiguous(asked, [group for group, _ in shown], again=_AGAIN), {}
        if not shown:
            return people_op._no_match(state, asked, context), {}
        group = shown[0][0]
        labels[asked] = people_op._label(group)
        cond.person.ids = [u["id"] for u in group]
    logger.debug("find_records people by name resolved=%d", len(labels))
    return None, labels


def _applied(query: RecordQuery) -> str:
    return json.dumps(query.model_dump(mode="json", by_alias=True, exclude_none=True)["where"], separators=(",", ":"))


def _number(value: float) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def row_facts(row: dict[str, Any]) -> str:
    """What a row states about its record, as its citation quotes it."""
    facts = [f"{name} {row[name]}" for name in _CITED_FACTS if row.get(name) not in (None, "")]
    return " · ".join(facts)


def render(
    query: RecordQuery, result: RecordQueryResult, *, shortener: Any = None,  # noqa: ANN401
    cite_refs: Mapping[str, str] | None = None, scope_note: str = "", people: Mapping[str, str] | None = None,
    recall: RecallCheck | None = None, possible: list[dict[str, Any]] | None = None,
    judged: JudgeOutcome | None = None,
) -> str:
    """Totals first, then what was computed, then a page of rows to cite."""
    cite_refs = cite_refs or {}
    total = f"at least {result.total}" if result.approximate else str(result.total)
    lines = [f"Total: {total} matching records you can open" + (
        " (approximate: the scan stopped at its limit; add a narrower condition such as a date window, "
        "a container or a person)" if result.approximate else " (exact)"
    )]
    if scope_note:
        # Every figure below counts only the sources the conversation is limited to.
        lines.append(scope_note)
    if query.where:
        lines.append(f"Conditions as applied: {_applied(query)}")
    for asked, label in (people or {}).items():
        lines.append(f'Person "{asked}" is {label}.')
    if any(isinstance(c, MentionCondition) for c in query.where):
        lines.append(MENTIONS_NOTE)
    # A stopped scan saw only part of the matches: every figure from it is partial, not just the total.
    kind = "approximate" if result.approximate else "exact"
    if query.aggregate.op != "count" and result.value is not None:
        lines.append(f"{query.aggregate.op} of {query.aggregate.field}: {_number(result.value)} ({kind})")
    if result.groups or result.unassigned:
        by = query.group_by
        label = by.field or by.month or ("organization" if by.organization else (by.person_role or "person"))
        lines.append(f"Groups by {label} (top {len(result.groups)}, {kind} counts):")
        lines += [f"- {g.label}: {'at least ' if result.approximate else ''}{g.count}"
                  + (f", {query.aggregate.op} {_number(g.value)}" if g.value is not None else "") + f" ({kind})"
                  for g in [*result.groups, *result.unassigned]]
    lacking = {name: n for name, n in result.unknown.items() if name not in (NOT_CLASSIFIED, CLASSIFICATION_FAILED)}
    if lacking:
        lines.append("Not counted, because the record does not carry the field: " + ", ".join(
            f"{n} lack {name}" for name, n in lacking.items()))
    if result.unknown.get(NOT_CLASSIFIED):
        lines.append(
            f"Not counted: {result.unknown[NOT_CLASSIFIED]} records not classified yet, so whether they "
            "match a topic, category or organisation condition is unknown (neither a match nor a miss)."
        )
    undecided = result.unknown.get(NOT_CLASSIFIED, 0) + result.unknown.get(CLASSIFICATION_FAILED, 0)
    if undecided > _UNKNOWN_DOMINATES * (result.total + undecided):
        lines.append("Most records here are not classified, so the answer is mostly unknown; "
                     "a classification pass (reindex) would make it exact.")
    if result.unknown.get(CLASSIFICATION_FAILED):
        lines.append(
            f"Not counted: {result.unknown[CLASSIFICATION_FAILED]} records whose classification failed, so "
            "whether they match a topic, category or organisation condition is unknown; a reindex retries them."
        )
    for name, values in result.described.items():
        lines.append(f"Values of {name} among the matches: " + (
            ", ".join(f"{g.key} ({g.count})" for g in values) or "none"))
    if result.rows:
        start = result.offset
        order = "" if query.sort == NEWEST else f", {query.sort}"
        lines.append(f"Records {start + 1}-{start + len(result.rows)} of {total}{order}:")
        cited = [cite_refs[r["record_id"]] for r in result.rows if r.get("record_id") in cite_refs]
        if cited:
            lines.append(CITE_NOTE.format(ref=cited[0]))
        for row in result.rows[:_TOP_ROWS_SHOWN_AS_IDS]:
            shown = dict(row)
            if shown.get("record_id") in cite_refs:
                shown["cite"] = cite_refs[shown["record_id"]]
            if shortener is not None and shown.get("record_id"):
                shown["record_id"] = shortener.shorten_record_ids_in_text(shown["record_id"])
            lines.append("- " + json.dumps(shown, ensure_ascii=False, separators=(",", ":")))
    if result.next_cursor:
        lines.append(f'More records: call again with the same arguments and cursor="{result.next_cursor}".')
    if recall is not None:
        lines += _recall_lines(recall, possible or [], cite_refs, shortener, judged)
    elif query.cursor:
        lines.append(RECALL_ON_FIRST_PAGE)
    return "\n".join(lines)


def _recall_lines(
    recall: RecallCheck, possible: list[dict[str, Any]], cite_refs: Mapping[str, str], shortener: Any,  # noqa: ANN401
    judged: JudgeOutcome | None = None,
) -> list[str]:
    if recall.failed:
        return [RECALL_DID_NOT_RUN]
    if recall.skipped:
        return [RECALL_SKIPPED]
    if not recall.possible:
        return []

    def listed(row: dict[str, Any]) -> str:
        shown = dict(row)
        verdict = judged.verdicts.get(str(row.get("record_id"))) if judged is not None and judged.ran else None
        if verdict is not None and verdict.quote:
            shown["judge_quote"] = verdict.quote
        if shown.get("record_id") in cite_refs:
            shown["cite"] = cite_refs[shown["record_id"]]
        if shortener is not None and shown.get("record_id"):
            shown["record_id"] = shortener.shorten_record_ids_in_text(shown["record_id"])
        return "- " + json.dumps(shown, ensure_ascii=False, separators=(",", ":"))

    if judged is None or not judged.ran:
        lines = [f"Possible matches the graph could not confirm: {len(recall.possible)} "
                 f"(search found them; the graph lacks the facts to decide). {POSSIBLE_RULE}"]
        if judged is not None:
            lines.append(JUDGE_DID_NOT_RUN.format(why="no model" if judged.error == "no_model" else
                                                  "timed out" if judged.error == "TimeoutError" else "error"))
        return lines + [listed(row) for row in possible]
    likely = [r for r in possible if judged.of(str(r.get("record_id"))) is Verdict.SUPPORTS]
    rest = [r for r in possible if r not in likely]
    contradicted = sum(1 for j in judged.verdicts.values() if j.verdict is Verdict.CONTRADICTS)
    # Contradicted ones are not possible any more; the rest past the shown ones were never judged.
    remaining = len(recall.possible) - contradicted
    breakdown = f"{len(likely)} likely by model judgement, {remaining - len(likely)} unclear"
    if contradicted:
        breakdown += f"; {contradicted} contradicted by their own text and dropped"
    lines = [f"Possible matches the graph could not confirm: {remaining} ({breakdown}). {POSSIBLE_RULE}",
             JUDGED_NOTE]
    if likely:
        lines.append(LIKELY_HEADER.format(n=len(likely)))
        lines += [listed(row) for row in likely]
    if rest:
        lines.append(STILL_POSSIBLE_HEADER.format(n=len(rest)))
        lines += [listed(row) for row in rest]
        lines.append(UNCLEAR_HINT)
    return lines


def roles_guide() -> str:
    return ", ".join(ROLE_EDGES)


def value_fields() -> list[str]:
    return [f.name for f in FIELDS.values() if f.kind in (FieldKind.VALUE, FieldKind.TEXT)]


__all__ = ["MENTIONS_CONDITION_TEXT", "execute_find_records", "field_guide", "render", "roles_guide", "value_fields"]
