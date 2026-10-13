"""``search_by_entities``: passages from the records that mention the named
entities an agent names, linked by ``app.modules.named_entities.linking``.

The agent passes names (several at once, a kind each if it knows one). Each is
linked to stored entities: an exact readable match settles it, anything else
goes through one model check that may say none of the look-alikes is the same
thing. The readable records that mention the linked entities, all of them
together for ``match="all"``, are then searched with the query (the names when
none is given) through ``execute_search``, which renders and cites them as any
search does. A resolution header opens the result.

Offered only when the deployment turns it on (``agent_loop/entity_search_tool``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from app.agents.actions.knowledge_graph.ops.entity_discovery import (
    NAMED_ENTITIES_DISABLED_MSG,
    _named_entities_enabled,
)
from app.agents.actions.knowledge_graph.ops.entity_filters import (
    load_entity_access_context,
)
from app.agents.actions.knowledge_graph.ops.entity_records import LOOKUP_FAILED_MSG
from app.agents.actions.knowledge_graph.ops.search import execute_search
from app.agents.actions.knowledge_graph.views import _short, _trunc
from app.agents.agent_loop.protocol.run_usage import (
    ENTITY_LINKING_SOURCE,
    llm_usage_reporter,
)
from app.modules.agents.qna.chat_state import remember_record_ids
from app.modules.named_entities.linking.candidates import (
    MAX_CANDIDATES,
    CandidateFinder,
)
from app.modules.named_entities.linking.mentions import parse_mentions
from app.modules.named_entities.linking.records import (
    NEARBY,
    NEARBY_BLOCKS,
    readable_records,
)
from app.modules.named_entities.linking.service import EntityLinker, LinkStatus
from app.modules.named_entities.linking.verifier import LlmMentionVerifier
from app.modules.retrieval.entity_permissions import (
    EntityAccessError,
    search_entities_for_user,
)
from app.modules.retrieval.provenance import note_retrieved_by
from app.utils.chat_helpers import get_record_id_shortener_if_enabled

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

    from app.agent_loop_lib.core.messages import Part
    from app.modules.agents.qna.chat_state import ChatState
    from app.modules.named_entities.linking.candidates import Candidate, DenseSearch
    from app.modules.named_entities.linking.records import RecordScope
    from app.modules.named_entities.linking.service import Linking, LinkResult
    from app.modules.retrieval.entity_permissions import EntityAccessContext, EntityHit
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.utils.chat_helpers import RecordIdShortener

logger = logging.getLogger(__name__)

NAMED_ENTITY = "named_entity"
DEADLINE_SECONDS = 20.0
TOGETHER_SHOWN = 3
VERIFIER_LLM_ROLE = "query"
# Inside the verifier's own deadline; a stalled config read must not use it up.
VERIFIER_LLM_LOOKUP_SECONDS = 3.0
NO_NAMES_MSG = (
    "Pass the names to look up as mentions=[{\"name\": \"Contoso\", \"kind\": \"organization\"}, ...]."
)
TOO_SLOW_MSG = "The entity lookup took too long — try again with fewer names."
# Records indexed before extraction was turned on have no entities until they
# are indexed again, so a miss here is not evidence of absence.
COVERAGE_NOTE = (
    "Only records indexed while named-entity extraction was on are linked to names, so a name "
    "not found here may still be written in older records; search the text before concluding "
    "it is absent."
)


async def execute_search_by_entities(
    state: ChatState,
    *,
    mentions: Any = None,  # noqa: ANN401 - tool argument, any JSON shape
    match: str | None = None,
    query: str | None = None,
) -> tuple[bool, str] | str | list[Part]:
    if not state:
        return False, LOOKUP_FAILED_MSG
    if not await _named_entities_enabled(state):
        return False, NAMED_ENTITIES_DISABLED_MSG
    parsed = parse_mentions(mentions)
    notes = list(parsed.notes)
    if not parsed.mentions:
        return False, " ".join([*notes, NO_NAMES_MSG])
    match_text = match.strip().casefold() if isinstance(match, str) and match.strip() else "any"
    if match_text not in {"any", "all"}:
        notes.append(f"match '{match}' is not 'any' or 'all'; used 'any'.")
        match_text = "any"
    match_all = match_text == "all" and len(parsed.mentions) > 1

    graph = state.get("graph_provider")
    if graph is None:
        return False, LOOKUP_FAILED_MSG
    started = time.monotonic()
    try:
        context = await load_entity_access_context(state)
        linker = EntityLinker(
            CandidateFinder(graph, context, dense=_dense_search(state, graph, context)),
            _verifier(state),
            graph,
            context.org_id,
        )
        linking = await asyncio.wait_for(
            linker.link(parsed.mentions, question=str(state.get("query") or "")), timeout=DEADLINE_SECONDS,
        )
        scope = await asyncio.wait_for(
            readable_records(graph, context, linking.results, match_all=match_all),
            timeout=max(1.0, DEADLINE_SECONDS - (time.monotonic() - started)),
        )
    except TimeoutError:
        logger.warning("search_by_entities: deadline after %dms", int((time.monotonic() - started) * 1000))
        return False, TOO_SLOW_MSG
    except EntityAccessError:
        logger.warning("search_by_entities: access check failed", exc_info=True)
        return False, LOOKUP_FAILED_MSG
    except Exception:
        # A failed lookup must read as a failure, never as "no records".
        logger.exception("search_by_entities: lookup failed")
        return False, LOOKUP_FAILED_MSG

    shortener = get_record_id_shortener_if_enabled(state)
    together = _together_records(scope)
    remember_record_ids(state, together, names={key: scope.record_names.get(key, "") for key in together})
    preamble = render_preamble(linking, scope, match_all=match_all, notes=notes, shortener=shortener)
    _log(linking, scope, started)

    if not scope.virtual_record_ids:
        return True, preamble + _nothing_to_search(linking, match_all=match_all)

    # Every name asked, linked or not: an unlinked one may still be in the text.
    names = [result.mention.name for result in linking.results]
    shown_before = set(state.get("virtual_record_id_to_result") or {})
    result = await execute_search(
        state, (query or "").strip() or " ".join(names),
        scope_virtual_record_ids=scope.virtual_record_ids, preamble=preamble,
    )
    in_scope = set(scope.virtual_record_ids)
    shown = state.get("virtual_record_id_to_result") or {}
    note_retrieved_by(state, "entity", [
        (vrid, record.get("id"), record.get("record_name"))
        for vrid, record in shown.items()
        if vrid not in shown_before and vrid in in_scope and isinstance(record, dict)
    ])
    return result


def _dense_search(state: ChatState, graph: IGraphDBProvider, context: EntityAccessContext) -> DenseSearch | None:
    store = state.get("entity_vector_store")
    if store is None:
        return None

    async def _search(name: str, kinds: list[str]) -> list[EntityHit]:
        return await search_entities_for_user(
            store, graph, context, name, entity_types=[NAMED_ENTITY], kinds=kinds, top_k=MAX_CANDIDATES,
        )

    return _search


def _verifier(state: ChatState) -> LlmMentionVerifier:
    from app.agent_loop_lib.transport.opik_tracing import build_langchain_opik_callbacks

    async def _model() -> tuple[BaseChatModel | None, str]:
        return await verifier_model(state)

    return LlmMentionVerifier(
        _model,
        on_llm_usage=llm_usage_reporter(state, ENTITY_LINKING_SOURCE),
        callbacks=build_langchain_opik_callbacks(),
    )


async def verifier_model(state: ChatState) -> tuple[BaseChatModel | None, str]:
    """The ``query``-role model at low effort (the default model when the role
    is unassigned), as grep-command generation uses; the chat's own model when
    none can be built, so a deployment without roles still checks names."""
    from app.utils.llm import get_llm_for_role

    config_service = state.get("config_service")
    if config_service is not None:
        try:
            llm, config = await asyncio.wait_for(
                get_llm_for_role(config_service, VERIFIER_LLM_ROLE, reasoning_effort="low"),
                timeout=VERIFIER_LLM_LOOKUP_SECONDS,
            )
            configuration = (config or {}).get("configuration") or {}
            return llm, str(configuration.get("model") or (config or {}).get("modelKey") or "")
        except Exception as exc:  # the chat's model below keeps the check running
            logger.info("search_by_entities: no low-effort model for the name check, using the chat's: %r", exc)
    return state.get("llm"), ""


def _together_records(scope: RecordScope) -> list[str]:
    nearby = [key for key in scope.record_ids if scope.together.get(key, (NEARBY + 1, -1))[0] <= NEARBY]
    return nearby[:TOGETHER_SHOWN]


def render_preamble(
    linking: Linking,
    scope: RecordScope,
    *,
    match_all: bool,
    notes: list[str],
    shortener: RecordIdShortener | None = None,
) -> str:
    counts = {name.mention: name.count_label for name in scope.per_name}
    lines = ["Entities:"]
    lines.extend(_line(result, linking.names, counts.get(result.mention.name, "0")) for result in linking.results)
    if match_all and scope.virtual_record_ids:
        lines.append(_together_line(scope, shortener))
    if linking.timed_out:
        lines.append("Note: access checks were cut short for this call, so results may be incomplete; retry.")
    if scope.note:
        lines.append(f"Note: {scope.note.strip()}")
    lines.extend(f"Note: {note}" for note in notes)
    if any(not result.accepted for result in linking.results) or not scope.virtual_record_ids:
        lines.append(f"Note: {COVERAGE_NOTE}")
    return "\n".join(lines) + "\n\n"


def _line(result: LinkResult, names: dict[str, str], count: str) -> str:
    mention = result.mention
    asked = f'"{_trunc(mention.name)}"' + (f" ({mention.kind_hint.value})" if mention.kind_hint else "")
    if result.status in (LinkStatus.MATCHED, LinkStatus.CHECKED):
        how = "exact match" if result.status is LinkStatus.MATCHED else "checked"
        entities = ", ".join(_entity(c, names) for c in result.accepted)
        line = f"- {asked}: {entities}; {how}; mentioned in {count} readable record{'' if count == '1' else 's'}."
        if result.rejected:
            line += f" Not the same: {_entities(result.rejected, names)}."
        if result.unchecked:
            line += (
                f" Longer names that may be the same could not be checked and were not used: "
                f"{_entities(result.unchecked, names)}; call again with the full name to include one."
            )
        if result.more_longer_names:
            line += " Many more readable names start with it; call again with the full name to include one."
        return line
    if result.status is LinkStatus.AMBIGUOUS:
        return f"- {asked}: too many readable entities start with this name; give the full name."
    if result.status is LinkStatus.UNVERIFIED:
        return (
            f"- {asked}: possible matches could not be checked and were not used: "
            f"{_entities(result.unchecked, names)}. Call again with the exact name of the one you mean."
        )
    line = f"- {asked}: no readable entity by this name."
    if result.rejected:
        line += f" Not the same: {_entities(result.rejected, names)}."
    return line


def _entity(candidate: Candidate, names: dict[str, str]) -> str:
    return f"{_trunc(names.get(candidate.entity_id, candidate.name))} ({candidate.kind or 'entity'})"


def _entities(candidates: list[Candidate], names: dict[str, str]) -> str:
    return ", ".join(_entity(c, names) for c in candidates)


def _together_line(scope: RecordScope, shortener: RecordIdShortener | None) -> str:
    nearby = _together_records(scope)
    line = f"Together: {len(scope.record_ids)} readable record(s) searched"
    if not nearby:
        return line + "."
    shown = "; ".join(
        f"{_trunc(scope.record_names.get(key) or key)} (block {scope.together[key][1]}): "
        f"knowledgegraph__fetch_record(record_ids=[\"{_short(key, shortener)}\"], "
        f"start_block={max(0, scope.together[key][1] - NEARBY_BLOCKS)})"
        for key in nearby
    )
    return f"{line}; within {NEARBY_BLOCKS} blocks of each other in: {shown}."


def _nothing_to_search(linking: Linking, *, match_all: bool) -> str:
    if not any(result.accepted for result in linking.results):
        return "No readable record mentions these names as entities."
    if match_all:
        return "No readable record mentions all of them; try match=\"any\" or fewer names."
    return "No readable record mentions them."


def _log(linking: Linking, scope: RecordScope, started: float) -> None:
    statuses: dict[str, int] = {}
    for result in linking.results:
        statuses[result.status.value] = statuses.get(result.status.value, 0) + 1
    logger.info(
        "search_by_entities names=%d statuses=%s verifier=%s timed_out=%s scope=%d nearby=%d latency_ms=%d",
        len(linking.results), statuses, linking.verifier_called, linking.timed_out,
        len(scope.virtual_record_ids), len(_together_records(scope)), int((time.monotonic() - started) * 1000),
    )


__all__ = ["execute_search_by_entities", "render_preamble", "verifier_model"]
