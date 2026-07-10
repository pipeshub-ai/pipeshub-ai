"""Intent understanding: one structured agent run (via `SingleShotLoop` +
`task_complete`) that reorganizes the raw user query into a clear, self-
contained restatement (resolving pronouns/follow-ups against conversation
history, extracting requirements/success criteria) and — for `chatMode ==
"auto"` — reuses the exact SAME tier rubric `classify_route()`
(`app.modules.agents.qna.router`) applies, so this one call can also pick
quick/react/deep without a second LLM round-trip.

Deliberately reuses `build_capability_context()`, `build_prior_routing_
messages()`, `build_tier_rubric()`, and `build_sql_verify_override()` from
`app.modules.agents.qna.router` rather than re-deriving any of them — the
tier definitions and conversation-history handling must never drift from
what the legacy LangGraph path and `classify_route()` already use.

Clarification: this same call also decides whether the request is too
ambiguous to safely reorganize (`gaps`/`clarifying_questions`). The
`clarifying_questions` field reuses `AskUserQuestionItemInput`/
`AskUserQuestionOptionInput` (`app.agents.actions.internal_tools.
intrim_tools`) — the EXACT schema the main agent's `internaltools.
ask_user_question` tool already validates against — so a clarification
produced here (before any `Agent` exists) and one produced by the main
agent mid-run (after tool orchestration) render as IDENTICAL interactive
question cards on the frontend. See `clarification.py` for where this
gets turned into the actual SSE payload.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Literal

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app.agent_loop_lib.agent.single_shot_runner import (
    StructuredSingleShotError,
    build_task_complete_runtime,
    run_structured_single_shot,
)
from app.agent_loop_lib.agent.spec import ModelSpec
from app.agent_loop_lib.core.structured_output import coerce_list
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.transport.opik_tracing import is_opik_configured, wrap_if_enabled
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.actions.internal_tools.intrim_tools import AskUserQuestionItemInput
from app.agents.agent_loop.converters import convert_message_from_langchain
from app.agents.agent_loop.langchain_transport import LangChainTransport
from app.modules.agents.qna.router import (
    build_capability_context,
    build_prior_routing_messages,
    build_sql_verify_override,
    build_tier_rubric,
)

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel


class IntentRouteDecision(BaseModel):
    """Structured output of `parse_intent_and_route()`.

    reasoning: brief chain-of-thought — what the user actually wants,
               resolved against conversation history, BEFORE restating it.
    rewritten_query: a clear, self-contained restatement of the request —
               pronouns/follow-ups ("do it", "the second one") resolved
               against history, but every explicit ID/name/value from the
               original query preserved verbatim. Falls back to the raw
               query when parsing fails or the model returns nothing usable.
    requirements: concrete things the answer must address (empty if none).
    success_criteria: how to tell the request was satisfied (empty if none).
    gaps: information missing that materially changes what to do and could
               NOT be inferred from context/history (empty when the request
               is workable as-is) — mirrors `agent_loop_lib`'s own
               `GoalBuilder`/`Goal.gaps` field, populated onto the `Goal`
               this decision produces (see `router.py::_build_goal`).
    clarifying_questions: populated ONLY when `gaps` is non-empty AND the
               ambiguity is severe enough to justify pausing the whole
               request for a user reply instead of letting the main agent
               proceed with a best-effort guess (or ask mid-run itself).
               Same shape the `internaltools.ask_user_question` tool takes.
    route: quick/react/deep — only populated when `include_routing=True`
               was passed to `parse_intent_and_route()`; `None` otherwise
               (explicit `chatMode`s already know their own loop).
    """

    reasoning: str
    rewritten_query: str
    requirements: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    clarifying_questions: list[AskUserQuestionItemInput] = Field(default_factory=list, max_length=5)
    route: Literal["quick", "react", "deep"] | None = None


_INTENT_INSTRUCTIONS = (
    "You are an intent-understanding agent. Read the user's request (and any "
    "prior conversation turns below) and produce:\n\n"
    "1. `reasoning`: one or two sentences on what the user is actually asking "
    "for, resolving any follow-up/pronoun reference against the conversation "
    "history.\n"
    "2. `rewritten_query`: a clear, self-contained restatement of the "
    "request that would make sense with NO conversation history attached. "
    "Resolve pronouns and follow-ups ('do it', 'the second one', 'yes') into "
    "what they refer to. Do NOT add assumptions, new constraints, or invented "
    "detail — and preserve every explicit ID, name, key, date, or value from "
    "the original query VERBATIM. If the original query is already "
    "self-contained, `rewritten_query` may be nearly identical to it.\n"
    "3. `requirements`: concrete things the final answer must address "
    "(empty list if the request is simple enough that none are needed).\n"
    "4. `success_criteria`: how to tell the request was satisfied (empty "
    "list if not applicable).\n"
    "5. `gaps`: information missing that materially changes what to do and "
    "could NOT be inferred from the query or conversation history (empty "
    "list if the request is workable as-is — most requests have no gaps).\n"
    "6. `clarifying_questions`: leave this EMPTY unless the request is "
    "genuinely too ambiguous or incomplete to act on — e.g. a required "
    "parameter for a write action that only the user can supply, or "
    "multiple incompatible interpretations that would lead to materially "
    "different outcomes. Do NOT populate this for anything answerable via "
    "search/lookup, even if the topic itself is broad — broad topics get "
    "rewritten and routed normally, not blocked on a question. When you do "
    "populate it (1-3 questions), each question needs 3-7 concrete, "
    "specific tappable options (never a catch-all like 'Other') and an "
    "explicit `multiSelect` (true if the user could reasonably pick more "
    "than one option, false for mutually-exclusive choices).\n\n"
)

_INTENT_OUTPUT_HINT = (
    "\n\nYour JSON output must include these fields:\n"
    '- "reasoning": string\n'
    '- "rewritten_query": string\n'
    '- "requirements": array of strings\n'
    '- "success_criteria": array of strings\n'
    '- "gaps": array of strings\n'
    '- "clarifying_questions": array of objects with "question", "options" '
    '(array of {"label": string}), and "multiSelect" boolean\n'
)


def _build_intent_prompt(include_routing: bool, capability_block: str, sql_verify_override: str, n_knowledge: int) -> str:
    if not include_routing:
        return _INTENT_INSTRUCTIONS + "Leave `route` null — it is not needed for this request."
    return (
        _INTENT_INSTRUCTIONS
        + "7. `route`: ALSO classify the (rewritten) request into exactly "
        "one execution tier — quick, react, or deep — using the rubric "
        "below. Still classify a route even when `clarifying_questions` is "
        "populated (it's ignored if the request is paused for a reply, but "
        "costs nothing to fill in).\n\n"
        + build_tier_rubric(capability_block, sql_verify_override, n_knowledge)
        + '\n\nInclude `"route"` in your JSON output as one of "quick", "react", or "deep".'
    )


def _decision_from_payload(payload: dict[str, Any], *, include_routing: bool) -> IntentRouteDecision:
    clarifying = payload.get("clarifying_questions") or []
    if not isinstance(clarifying, list):
        clarifying = []
    route = payload.get("route")
    if not include_routing:
        route = None
    elif route not in ("quick", "react", "deep"):
        route = None
    return IntentRouteDecision(
        reasoning=str(payload.get("reasoning") or ""),
        rewritten_query=str(payload.get("rewritten_query") or ""),
        requirements=[str(x) for x in coerce_list(payload.get("requirements"))],
        success_criteria=[str(x) for x in coerce_list(payload.get("success_criteria"))],
        gaps=[str(x) for x in coerce_list(payload.get("gaps"))],
        clarifying_questions=[AskUserQuestionItemInput.model_validate(q) for q in clarifying if isinstance(q, dict)],
        route=route,
    )


def _fallback_decision(user_query: str, *, include_routing: bool, reason: str) -> IntentRouteDecision:
    return IntentRouteDecision(
        reasoning=reason,
        rewritten_query=user_query,
        route="react" if include_routing else None,
    )


async def parse_intent_and_route(
    query_info: dict[str, Any],
    logger: Logger,
    llm: "BaseChatModel",
    *,
    include_routing: bool,
    config_service: Any = None,
    graph_provider: Any = None,
    is_multimodal_llm: bool = False,
    org_id: str = "",
    model_name: str = "",
) -> IntentRouteDecision:
    """Single structured agent run that understands/reorganizes the query
    and, when `include_routing=True`, also picks the execution tier.

    Runs through the shared `Agent` + `SingleShotLoop` + `task_complete`
    path (`agent_loop_lib/agent/single_shot_runner.py`) — never a bespoke
    `with_structured_output()` loop. Falls back to the raw query
    (route='react' when routing was requested) if the query is empty or the
    agent run/parsing fails.
    """
    user_query = query_info.get("query", "").strip()
    if not user_query:
        return _fallback_decision(user_query, include_routing=include_routing, reason="empty query")

    from app.modules.transformers.blob_storage import BlobStorage
    from app.utils.attachment_utils import resolve_attachments

    capability_block, n_knowledge, _indexed_connectors, _kb_sources, _tools_data = (
        build_capability_context(query_info)
    )
    sql_verify_override = build_sql_verify_override(query_info)
    system_prompt = _build_intent_prompt(include_routing, capability_block, sql_verify_override, n_knowledge)

    blob_store = None
    if config_service and graph_provider:
        try:
            blob_store = BlobStorage(
                logger=logger, config_service=config_service, graph_provider=graph_provider,
            )
        except Exception as _bs_exc:
            logger.warning("Intent: failed to create blob_store: %s", _bs_exc)

    prior_messages = await build_prior_routing_messages(
        query_info, blob_store=blob_store, org_id=org_id, is_multimodal_llm=is_multimodal_llm,
    )

    human_content: Any = f"user query : {user_query}"
    attachments = query_info.get("attachments") or []
    if blob_store:
        try:
            attachment_blocks: list[dict] = []
            if attachments and is_multimodal_llm:
                attachment_blocks = await resolve_attachments(
                    attachments=attachments, blob_store=blob_store, org_id=org_id,
                    is_multimodal_llm=True, logger=logger,
                )
            if attachment_blocks:
                human_content = [
                    {"type": "text", "text": f"user query : {user_query}\n\nAttached files from the user:\n"},
                    *attachment_blocks,
                ]
        except Exception as exc:
            logger.warning("Intent: failed to resolve attachments for intent context: %s", exc)

    seed_messages = [convert_message_from_langchain(m) for m in prior_messages]
    seed_messages.append(convert_message_from_langchain(HumanMessage(content=human_content)))

    opik_active = is_opik_configured()
    opik_project_name = os.getenv("OPIK_PROJECT_NAME")
    transport_registry = TransportRegistry()
    transport_registry.register(
        "langchain",
        lambda: wrap_if_enabled(
            LangChainTransport(llm, model_name=model_name, opik_project_name=opik_project_name),
            enabled=opik_active,
            project_name=opik_project_name,
        ),
    )
    runtime = build_task_complete_runtime(
        transport_registry,
        opik_enabled=opik_active,
        opik_project_name=opik_project_name,
    )

    try:
        payload = await run_structured_single_shot(
            name="intent.parse_intent_and_route",
            system_prompt=system_prompt,
            goal=Goal(description=user_query),
            runtime=runtime,
            model_spec=ModelSpec(provider="langchain", model=model_name),
            output_schema_hint=_INTENT_OUTPUT_HINT,
            seed_messages=seed_messages,
        )
        decision = _decision_from_payload(payload, include_routing=include_routing)
        if not decision.rewritten_query.strip():
            decision.rewritten_query = user_query
        logger.info(
            "Intent parsed: rewritten_query=%s | route=%s | reasoning=%s",
            decision.rewritten_query[:120], decision.route, decision.reasoning[:120],
        )
        return decision
    except StructuredSingleShotError as e:
        logger.warning("Intent parse failed, falling back to raw query: %s", e)
        return _fallback_decision(
            user_query,
            include_routing=include_routing,
            reason=f"intent parse failed: {e}",
        )
    except Exception as e:
        logger.warning("Intent parse failed, falling back to raw query: %s", e)
        return _fallback_decision(
            user_query,
            include_routing=include_routing,
            reason=f"intent parse failed: {e}",
        )


__all__ = ["IntentRouteDecision", "parse_intent_and_route"]
