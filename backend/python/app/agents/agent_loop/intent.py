"""Intent understanding: one free-form agent run (via `SingleShotLoop` +
`task_complete`) that reorganizes the raw user query into a clear, self-
contained restatement (resolving pronouns/follow-ups against conversation
history, surfacing requirements/success criteria) and — for `chatMode ==
"auto"` — also picks quick/react/deep without a second LLM round-trip.

Deliberately reuses `build_capability_context()`, `build_prior_routing_
messages()`, `build_tier_rubric()`, and `build_sql_verify_override()` from
`app.modules.agents.qna.router` rather than re-deriving any of them — the
tier definitions and conversation-history handling must never drift from
what the legacy LangGraph path and `classify_route()` already use.

Output format: the prompt asks for a detailed markdown write-up (headed
sections for the restatement/requirements/success criteria/open
questions, when applicable) and — for auto mode — a trailing tier word.
The output is still NOT parsed into structured fields: the model's
entire markdown text becomes `rewritten_query` (i.e. `Goal.description`)
verbatim, headers and all, and the only extraction is a single-word
regex scan for the `route`. The richer shape is purely for the model's
(and a human reading the trace's) benefit — downstream consumers
(prompt builder, planner prompts) receive the whole thing as one block
of context either way, so there is no schema to drift from or fail
against.
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING, Any, Literal

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field, ValidationError

from app.agent_loop_lib.agent.single_shot_runner import (
    StructuredSingleShotError,
    build_task_complete_runtime,
    run_text_single_shot,
)
from app.agent_loop_lib.agent.spec import ModelSpec
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

logger = logging.getLogger(__name__)


class IntentRouteDecision(BaseModel):
    """Output of `parse_intent_and_route()`.

    rewritten_query: the model's full free-form output — a self-contained
               restatement of the request with reasoning, requirements,
               and success criteria baked in. Becomes `Goal.description`.
    route: quick/react/deep — only populated when `include_routing=True`
               was passed; `None` otherwise.
    clarifying_questions: empty unless the model judged the request FATALLY
               ambiguous (see `_CLARIFY_INSTRUCTIONS`) and emitted a
               ```clarify fenced JSON block instead of its normal briefing
               — `_decision_from_text` is the only place that ever
               populates this. Non-empty here means `select_loop_and_goal()`
               /`stream_bridge.py` end the turn via
               `emit_pre_run_clarification()` without ever constructing an
               `Agent` — see that function's docstring. This is the
               upfront, cheaper counterpart to the main agent's OWN
               mid-run `ask_user_question` tool (still available for
               ambiguity that only surfaces once the agent starts working).
    """

    reasoning: str = ""
    rewritten_query: str = ""
    requirements: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    clarifying_questions: list[AskUserQuestionItemInput] = Field(default_factory=list, max_length=5)
    route: Literal["quick", "react", "deep"] | None = None


_INTENT_INSTRUCTIONS = (
    "You are an intent-understanding agent. Read the user's request (and any "
    "prior conversation turns below) and write a detailed markdown briefing "
    "for the agent that will execute it.\n\n"
    "1. Resolve pronouns and follow-ups ('do it', 'the second one', 'yes') "
    "against the conversation history.\n"
    "2. Preserve every explicit ID, name, key, date, or value from the "
    "original query VERBATIM.\n"
    "3. Do NOT add assumptions, new constraints, or invented detail — only "
    "surface what the request already implies.\n\n"
    "Structure your markdown with these headings, skipping any that don't "
    "apply to this request (this is guidance, not a schema — write "
    "whatever headings and depth best capture the request):\n"
    "## Request\n"
    "A clear, self-contained restatement of what the user is asking for.\n"
    "## Requirements\n"
    "Bullet points for any explicit or clearly implied requirements.\n"
    "## Success Criteria\n"
    "Bullet points for what a correct/complete answer looks like.\n"
)

# Mirrors the fatal-ambiguity criteria `InternalTools.ask_user_question`
# already uses for its OWN mid-run judgment call (see that tool's
# description, `intrim_tools.py`) — deliberately the SAME bar, so a
# request this pre-run gate lets through is never one the main agent
# would have immediately turned around and asked about anyway, and vice
# versa. Kept narrow on purpose: a vague-but-searchable topic ("tell me
# about the new pricing") must NOT trigger this — that goes to normal
# retrieval, not a question.
_CLARIFY_INSTRUCTIONS = (
    "\n\nEXCEPTION — fatal ambiguity: if the request is too incomplete to "
    "act on AT ALL (no topic, no action, a bare fragment with no "
    "antecedent in the conversation history — NOT merely a vague-but-"
    "searchable topic, which should get the normal briefing above instead), "
    "then SKIP the entire briefing above and instead write ONLY a single "
    "fenced code block tagged `clarify` containing one JSON object, "
    "nothing else before or after it:\n"
    "```clarify\n"
    '{"user_intent": "<your understanding of what they might mean>", '
    '"questions": [{"question": "<question text>", "multiSelect": false, '
    '"options": [{"label": "<option 1>"}, {"label": "<option 2>"}, '
    '{"label": "<option 3>"}]}]}\n'
    "```\n"
    "1-3 questions max, each with 3-7 concrete tappable options — expand "
    "the example's 3 options up to 7 if more concrete choices apply; never "
    "an 'Other'/'Something else' catch-all (the UI adds one automatically). "
    "Use this exception RARELY — only when there is truly nothing to work "
    "with."
)

# A ```clarify fenced block containing the escape-hatch JSON described in
# `_CLARIFY_INSTRUCTIONS` above. Same pattern as `_ROUTE_LINE_RE`: a
# distinct sentinel tag (not generic ```json) so a normal briefing that
# happens to mention or quote JSON is never mistaken for this.
_CLARIFY_BLOCK_RE = re.compile(r"```clarify\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def _parse_clarify_block(text: str) -> list["AskUserQuestionItemInput"] | None:
    """Extracts and validates the ```clarify block, if the model emitted
    one. Returns `None` (never raises) on absence OR on any malformed/
    invalid JSON — a model that gets the escape hatch's format wrong
    should fall back to being treated as a normal briefing, not blow up
    the whole intent call."""
    match = _CLARIFY_BLOCK_RE.search(text)
    if not match:
        return None
    try:
        import json

        payload = json.loads(match.group(1))
        questions = payload.get("questions") if isinstance(payload, dict) else None
        if not questions:
            return None
        return [AskUserQuestionItemInput.model_validate(q) for q in questions]
    except (ValueError, ValidationError, AttributeError, TypeError) as e:
        logger.warning("Intent: malformed ```clarify block, ignoring: %s", e)
        return None


def _build_intent_prompt(include_routing: bool, capability_block: str, sql_verify_override: str, n_knowledge: int) -> str:
    if not include_routing:
        return _INTENT_INSTRUCTIONS + _CLARIFY_INSTRUCTIONS
    return (
        _INTENT_INSTRUCTIONS
        + "\nAfter your restatement, on a NEW line, write ONLY one of the words "
        "`quick`, `react`, or `deep` to classify this request into the "
        "correct execution tier using the rubric below.\n\n"
        + build_tier_rubric(capability_block, sql_verify_override, n_knowledge)
        + _CLARIFY_INSTRUCTIONS
    )


_ROUTE_RE = re.compile(r"\b(quick|react|deep)\b", re.IGNORECASE)

# A line that is NOTHING but one tier word (optional backticks/whitespace) —
# the marker the routing prompt asks for. Distinct from `_ROUTE_RE` so a
# briefing that merely mentions "a quick check" or "deep dive" in prose is
# never mistaken for the routing marker.
_ROUTE_LINE_RE = re.compile(r"^[ \t]*`?(quick|react|deep)`?[ \t]*$", re.IGNORECASE | re.MULTILINE)


def _extract_route_and_clean(text: str) -> tuple[str | None, str]:
    """Pulls the tier marker out of the model's output AND removes it from
    the text that becomes `rewritten_query`/`Goal.description`.

    The removal is the load-bearing part: the tier word is an internal
    routing token, but left inside the goal text it reads as a USER
    instruction to every downstream consumer — a stray standalone `quick`
    in the goal made the quick-mode planner phrase its final phase as
    "keeping it quick and focused per the user's request", and the
    executing model then compressed a detailed evidence pack into a vague
    summary. Prefers a standalone marker line (and strips ALL of them);
    falls back to a plain word scan (without stripping prose) only when
    the model ignored the "on a NEW line" formatting instruction."""
    line_matches = _ROUTE_LINE_RE.findall(text)
    if line_matches:
        return line_matches[-1].lower(), _ROUTE_LINE_RE.sub("", text).strip()
    word_matches = _ROUTE_RE.findall(text)
    return (word_matches[-1].lower() if word_matches else None), text


def _decision_from_text(text: str, *, include_routing: bool) -> IntentRouteDecision:
    """The model's output becomes `rewritten_query` — minus the tier marker
    when routing was requested (see `_extract_route_and_clean`) — UNLESS
    the model took the fatal-ambiguity escape hatch (`_CLARIFY_INSTRUCTIONS`),
    in which case `clarifying_questions` is populated instead and
    `rewritten_query` becomes the block's `user_intent` (or, failing that,
    the raw model text) — `_build_goal()`/`select_loop_and_goal()` still
    need SOME `Goal.description`, even though the caller short-circuits
    before that `Goal` ever reaches an `Agent`."""
    clarifying_questions = _parse_clarify_block(text)
    if clarifying_questions:
        match = _CLARIFY_BLOCK_RE.search(text)
        user_intent = ""
        if match:
            import json

            try:
                user_intent = str(json.loads(match.group(1)).get("user_intent") or "")
            except (ValueError, AttributeError, TypeError):
                pass
        return IntentRouteDecision(
            rewritten_query=user_intent,
            clarifying_questions=clarifying_questions,
            route=None,
        )
    if not include_routing:
        return IntentRouteDecision(rewritten_query=text.strip(), route=None)
    route, cleaned = _extract_route_and_clean(text)
    return IntentRouteDecision(
        rewritten_query=cleaned.strip() or text.strip(),
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
    logger: "Logger",
    llm: "BaseChatModel",
    *,
    include_routing: bool,
    config_service: Any = None,
    graph_provider: Any = None,
    is_multimodal_llm: bool = False,
    org_id: str = "",
    model_name: str = "",
    transport_registry: TransportRegistry | None = None,
    opik_active: bool | None = None,
    opik_project_name: str | None = None,
) -> IntentRouteDecision:
    """Single agent run that understands/reorganizes the query and, when
    `include_routing=True`, also picks the execution tier.

    The model's full output is used as the rewritten query (no structured
    parsing). Falls back to the raw query (route='react' when routing was
    requested) if the query is empty or the agent run fails.

    `transport_registry`, when given, is expected to already have
    "langchain" registered (as `PipesHubAgentFactory.create()`/
    `select_loop_and_goal()` do for their own main-agent transport) and is
    reused as-is — `TransportRegistry.resolve()` caches by provider, so
    this avoids constructing (and, if Opik is on, re-wrapping) a second
    `LangChainTransport` for the SAME `llm` just for this one intent call.
    `None` (the default, and every direct caller/test outside the main
    request path) falls back to building a throwaway one exactly as before.
    """
    user_query = query_info.get("query", "").strip()
    if not user_query:
        return _fallback_decision(user_query, include_routing=include_routing, reason="empty query")

    from app.modules.transformers.blob_storage import BlobStorage
    from app.utils.attachment_utils import resolve_attachments

    capability_block, n_knowledge, _sources, _tools_data = (
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

    if opik_active is None:
        opik_active = is_opik_configured()
    if opik_project_name is None:
        opik_project_name = os.getenv("OPIK_PROJECT_NAME")
    if transport_registry is None or not transport_registry.has("langchain"):
        transport_registry = transport_registry or TransportRegistry()
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
        text = await run_text_single_shot(
            name="intent.parse_intent_and_route",
            system_prompt=system_prompt,
            goal=Goal(description=user_query),
            runtime=runtime,
            model_spec=ModelSpec(provider="langchain", model=model_name),
            seed_messages=seed_messages,
        )
        decision = _decision_from_text(text, include_routing=include_routing)
        if not decision.rewritten_query.strip():
            decision.rewritten_query = user_query
        logger.info(
            "Intent parsed: route=%s | rewritten_query=%s",
            decision.route, decision.rewritten_query[:120],
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


async def parse_intent(
    query_info: dict[str, Any],
    logger: "Logger",
    llm: "BaseChatModel",
    **kwargs: Any,
) -> IntentRouteDecision:
    """Intent-only wrapper: rewrites the query without routing classification."""
    return await parse_intent_and_route(
        query_info, logger, llm, include_routing=False, **kwargs,
    )


__all__ = ["IntentRouteDecision", "parse_intent", "parse_intent_and_route"]
