"""ADR-RUN-03 D1: ground before delegating.

Before the chat hands a request to a job, the goal step checks that the subject the request names is in what the
person can reach, from the search the chat already ran (the mode's upfront retrieval) or one cheap search for the
subject. When it is not found and the turn may ask, the run asks the person first with the chat's own question card
(the pre-run clarification), and no job starts. Their answer starts the next run, which goes on: with what was found
("Use what you can find", so the answer leads with the not-found line), with the record they picked, or with the
subject they typed. A turn asks at most once for grounding, and never when it answers a card.

The request a hand-off carries for a run that answers a card is the request the card belongs to, with the person's
answer after it: the engine is never given "User selections: …" alone as the task.
"""

from __future__ import annotations

import contextlib
import copy
import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.harness.assembly.code_task import PENDING_QUESTION_KEY
from app.harness.assembly.grounding_sources import (
    Searched,
    first_use_searches,
    runs_without_asking,
    search_sources,
    source_searches,
)
from app.harness.grounding import (
    JOB_ASK_INTENT,
    UNCHECKED,
    Grounding,
    Place,
    asked_subject,
    assess,
    card_answers,
    grounding_answer,
    grounding_question,
    hits_from_state,
    request_subject,
    same_subject,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from contextlib import AbstractContextManager

    from pydantic import JsonValue

    from app.agents.agent_loop.mcp_lazy import LazyMcpServers
    from app.agents.chat_modes.prefetch import PrefetchResult
    from app.harness.assembly.chat import ChatTurn
    from app.harness.assembly.context import RunContext
    from app.harness.grounding import Hit
    from harness_core.logs import HarnessLog
    from harness_core.tools.registry import ToolRegistry


def answers_card(query: str) -> bool:
    from app.agents.agent_loop.factory import is_ask_user_question_resume_query

    return is_ask_user_question_resume_query(query)


def hand_off_request(run: RunContext) -> str:
    """The person's request a hand-off works on: the run's message, or, when it answers a question card, the request
    that card asked about followed by the answer, both as the person wrote them."""
    from app.agents.agent_loop.factory import last_real_user_query

    query = run.query
    if not answers_card(query):
        return query
    history = [dict(turn) for turn in run.previous_conversations]
    original = last_real_user_query(history, "", answers=query).strip()
    return f"{original}\n\n{query}" if original and original != query.strip() else query


def answers_job_question(run: RunContext) -> bool:
    """Whether this run answers the card a job's question was asked with (ADR-RUN-03 D2): the reply answers a
    question the last reply asked under ``JOB_ASK_INTENT``. Its hand-off then goes ahead with the answer."""
    from harness_core.gateway.asking import question_key

    if not answers_card(run.query):
        return False
    answered = set(card_answers(run.query))
    for turn in reversed(run.previous_conversations):
        if turn.get("role") != "bot_response":
            continue
        rows = turn.get("tool_results")
        for row in rows if isinstance(rows, list) else []:
            args = row.get("args") if isinstance(row, dict) else None
            if not isinstance(args, dict) or args.get("user_intent") != JOB_ASK_INTENT:
                continue
            questions = args.get("questions")
            asked = {
                question_key(str(q.get("question") or ""))
                for q in (questions if isinstance(questions, list) else [])
                if isinstance(q, dict)
            }
            if asked & answered:
                return True
        return False
    return False


def asked_before(run: RunContext, subject: str) -> bool:
    """Whether this conversation already put a grounding question about ``subject`` to the person, however they
    dealt with it: answered, skipped, or a new message typed instead. Asking it again would ask the same thing
    twice; their earlier choice holds, and the run goes on with what it can find."""
    from app.agents.agent_loop.factory import (
        _turn_asked_questions,  # pyright: ignore[reportPrivateUsage]
    )

    for turn in run.previous_conversations:
        if turn.get("role") != "bot_response":
            continue
        # A card the model or the director asked is a tool row; the pre-run card's question is the turn's text.
        asked = [*(_turn_asked_questions(dict(turn)) or set()), str(turn.get("content") or "")]
        if any((found := asked_subject(text)) is not None and same_subject(found, subject) for text in asked):
            return True
    return False


def can_ask(run: RunContext, *, has_ui_client: bool) -> bool:
    """Whether this turn may still put a card to the person: someone is in the chat to answer, it is not a sub-agent,
    and its request has not used its cards (``MAX_CARDS_PER_REQUEST``, counting the card it answers)."""
    from app.agents.agent_loop.factory import cards_in_request
    from app.agents.agent_loop.hooks.ask_user_question import MAX_CARDS_PER_REQUEST

    if run.unattended or run.sub_agent is not None or not has_ui_client:
        return False
    return cards_in_request([dict(turn) for turn in run.previous_conversations]) < MAX_CARDS_PER_REQUEST


def may_ask(run: RunContext, *, has_ui_client: bool) -> bool:
    """Whether this turn may ask a grounding question: it can ask, and it does not answer a card (an answer goes on
    with what it chose)."""
    return can_ask(run, has_ui_client=has_ui_client) and not answers_card(run.query)


@dataclass(frozen=True)
class GateResult:
    """What the goal step journals: the grounding, and the question to ask before any job starts (``None``: go on).
    A run routed to a job asks it at once; one that stays on the loop keeps it for a ``code_task`` its model calls."""

    grounding: Grounding
    intent: str | None = None
    question: dict[str, JsonValue] | None = None
    # The run answers a grounding question: the card was asked before a hand-off, so the run hands off at once.
    answered: bool = False

    def pending(self) -> dict[str, JsonValue] | None:
        """The question as the goal slot keeps it for ``code_task`` (``PENDING_QUESTION_KEY``)."""
        if self.question is None:
            return None
        return {"intent": self.intent or "", "question": self.question}


class GroundingGate:
    """The check one run makes before it delegates. ``chat``: a built-in assistant run's turn, whose knowledge is
    searched with the mode's own retrieval; otherwise (an agent's run, the assistant agent included) its own
    knowledge search tool is called once, within the agent's knowledge scope, if the run is granted one
    (``search``: the tools it may call, by name; ``scope``: entered around the call, as around the run's own;
    ``lazy``: the run's MCP servers listed at first use, whose searches are built only when the check calls them)."""

    def __init__(
        self,
        run: RunContext,
        context: Any,  # noqa: ANN401 - AgentContext
        chat: ChatTurn | None,
        log: HarnessLog,
        *,
        registry: ToolRegistry | None = None,
        offered: Sequence[str] = (),
        scope: Callable[[], AbstractContextManager[object]] | None = None,
        lazy: LazyMcpServers | None = None,
    ) -> None:
        self._run = run
        self._context = context
        self._chat = chat
        self._log = log
        self._registry = registry
        self._offered = frozenset(offered)
        self._scope = scope
        self._lazy = lazy

    async def check(
        self,
        prefetched: PrefetchResult | None,
        *,
        delegating: bool,
        may_delegate: bool = False,
        probe: bool | None = None,
    ) -> GateResult | None:
        """``delegating``: routed to a job up front, so a search for the subject may be made when the chat made none;
        ``may_delegate``: the model may hand off itself, and the search the chat already ran is checked (no other: a
        turn that stays a chat reply pays nothing); ``probe``: search for the subject when the chat ran no search
        (default: when ``delegating``; the goal step also probes a request with a deep-work signal, which the model
        may well hand off). ``None`` when there is nothing to journal."""
        answer = grounding_answer(self._run.query) if answers_card(self._run.query) else None
        if answer is not None:
            subject = answer.subject if answer.choice == "use_found" else answer.value
            grounding = await self._ground(subject, prefetched, probe=True)
            if answer.choice == "use_found":
                grounding = _proceeded(grounding)
            self._log.info(
                "grounding.answered", choice=answer.choice, status=grounding.status, hits=grounding.hits,
                relevant=grounding.relevant,
            )
            return GateResult(grounding, answered=True)
        job_answer = answers_job_question(self._run)
        if not (delegating or may_delegate or job_answer):
            return None
        subject = request_subject(hand_off_request(self._run))
        grounding = await self._ground(subject, prefetched, probe=probe if probe is not None else delegating)
        self._log.info(
            "grounding.checked", status=grounding.status, searches=grounding.searches, hits=grounding.hits,
            relevant=grounding.relevant, near=len(grounding.near), delegating=delegating,
        )
        if grounding.not_found and grounding.subject is not None and asked_before(self._run, grounding.subject):
            # The person was asked about this subject already and did not name another: go on with what was found.
            self._log.info("grounding.asked_before", status=grounding.status)
            return GateResult(_proceeded(grounding), answered=job_answer)
        if grounding.not_found and may_ask(self._run, has_ui_client=bool(getattr(self._context, "has_ui_client", False))):
            intent, question = grounding_question(grounding)
            return GateResult(grounding, intent=intent, question=question)
        return GateResult(grounding, answered=job_answer)

    async def _ground(self, subject: str | None, prefetched: PrefetchResult | None, *, probe: bool) -> Grounding:
        """The subject against the person's PipesHub knowledge, then, when it is not there, against every connected
        source that can search for it (``grounding_sources``): found in one, it is found there; else the places
        looked in are named."""
        if subject is None:
            return UNCHECKED
        hits, searches, failed = await self._knowledge(subject, prefetched, probe=probe)
        knowledge = assess(subject, hits, searches=searches, failed=failed)
        if knowledge.status == "found" or not (probe or prefetched is not None):
            return knowledge
        searched = await self._sources(subject)
        ran = [s for s in searched if s.hits is not None]
        if not ran:
            return knowledge
        places = tuple(dict.fromkeys(s.search.place for s in ran))
        found_in = [
            Place(source.search.place, source.search.tool)
            for source in ran
            if assess(subject, source.hits or [], searches=1).status == "found"
        ]
        every = [*hits, *(h for s in ran for h in s.hits or [])]
        grounding = assess(subject, every, searches=searches + len(ran), failed=failed and not every)
        return dataclasses.replace(grounding, places=places, found_in=tuple(dict.fromkeys(found_in)))

    async def _knowledge(
        self, subject: str, prefetched: PrefetchResult | None, *, probe: bool
    ) -> tuple[list[Hit], int, bool]:
        """The person's PipesHub knowledge: its hits, how many searches ran and whether the search failed."""
        if prefetched is not None:
            # The chat's own search for the request: an empty one is a search that found nothing.
            return hits_from_state({"final_results": prefetched.final_results}), 1, _failed(prefetched)
        if not probe:
            return [], 0, False
        if self._chat is not None:
            if not self._chat.policy.has_knowledge:
                return [], 0, False
            found = await self._probe(subject)
            if found is None:
                return [], 0, False
            return hits_from_state({"final_results": found.final_results}), 1, _failed(found)
        searched = await self._tool_probe(subject)
        if searched is None:
            return [], 0, False
        return searched[0], 1, searched[1]

    async def _sources(self, subject: str) -> list[Searched]:
        """Every connected source's search the person's rules let run unasked, called once with the subject."""
        if self._registry is None:
            return []
        offered = set(self._offered)
        if self._lazy:
            await first_use_searches(self._context, self._registry, self._lazy)
            offered |= self._lazy.granted_names()
        searches = [
            s
            for s in source_searches(self._context, self._registry, sorted(offered))
            if await runs_without_asking(self._context, self._registry, s)
        ]
        if not searches:
            return []
        return await search_sources(searches, subject, registry=self._registry, scope=self._scope, log=self._log)

    async def _tool_probe(self, subject: str) -> tuple[list[Hit], bool] | None:
        """The run's own knowledge search for the subject alone: the agent's sources, its scope and the caller's
        access, as the model's call would have them. What it adds to the run's citation state is put back after it:
        the model never saw these results, so its answer cannot cite them."""
        name = next((n for n in PROBE_TOOLS if n in self._offered), None)
        if name is None or self._registry is None or not self._registry.has(name):
            return None
        state = self._context.tool_state
        saved = _snapshot(state)
        try:
            tool = self._registry.resolve_by_name(name)
            with self._scope() if self._scope is not None else contextlib.nullcontext():
                out = await tool.execute(query=subject)
            return hits_from_state(state), not out.success
        except Exception as exc:
            self._log.warning("grounding.probe_failed", error=type(exc).__name__)
            return None
        finally:
            state.clear()
            state.update(saved)

    async def _probe(self, subject: str) -> PrefetchResult | None:
        """One search for the subject alone, within the turn's own knowledge filters. Its results are read here and
        not added to the run's citation state: the model never saw them."""
        from app.agents.chat_modes.prefetch import prefetch_retrieval
        from app.harness.knowledge.caller import caller_user_id_of

        chat = self._chat
        assert chat is not None
        query_info = chat.setup.query_info
        try:
            return await prefetch_retrieval(
                query=subject,
                org_id=self._context.org_id,
                user_id=self._context.user_id,
                retrieval_service=chat.retrieval_service,
                graph_provider=chat.graph_provider,
                blob_store=chat.setup.blob_store,
                filters=query_info.get("filters"),
                limit=PROBE_LIMIT,
                is_multimodal_llm=False,
                previous_conversations=None,
                logger=_quiet(),
                force=True,
                caller_user_id=caller_user_id_of(self._context.user_info),
                can_fetch=False,
            )
        except Exception as exc:
            self._log.warning("grounding.probe_failed", error=type(exc).__name__)
            return None


PROBE_LIMIT = 10
# The router's signals of a request for research (``deep_route.classify``): its sources are the web, not only the
# person's knowledge, so it is not asked about up front (the job's own grounding still leads its answer).
RESEARCH_SIGNALS = frozenset({"research", "deep_research", "multi_url"})
# An agent's knowledge search tools, most direct first (``entity_search_surface.SEARCH_TOOL_NAME``, retrieval's).
PROBE_TOOLS = ("knowledgegraph__search", "retrieval__search_internal_knowledge")


def _snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """``state`` as it is, its containers and citation mapper copied: the search writes some keys anew and grows
    others in place (the record map, the ref mapper)."""
    from app.utils.chat_helpers import CitationRefMapper

    saved: dict[str, Any] = {}
    for key, value in state.items():
        if isinstance(value, (dict, list, set, CitationRefMapper)):
            try:
                saved[key] = copy.deepcopy(value)
                continue
            except Exception:
                pass
        saved[key] = value
    return saved


def _failed(result: PrefetchResult) -> bool:
    """The search could not run, or its filter was refused: nothing is known to be missing."""
    return result.failure_notice is not None or result.filter_notice is not None


def _proceeded(grounding: Grounding) -> Grounding:
    return dataclasses.replace(grounding, proceeded=True)


def _quiet() -> Any:  # noqa: ANN401 - a logging.Logger
    import logging

    log = logging.getLogger("app.harness.grounding.probe")
    log.propagate = False
    log.addHandler(logging.NullHandler())
    return log


__all__ = [
    "PENDING_QUESTION_KEY",
    "RESEARCH_SIGNALS",
    "PROBE_LIMIT",
    "GateResult",
    "GroundingGate",
    "answers_card",
    "answers_job_question",
    "asked_before",
    "can_ask",
    "hand_off_request",
    "may_ask",
]
