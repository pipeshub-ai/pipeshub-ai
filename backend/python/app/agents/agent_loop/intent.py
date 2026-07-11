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
from pydantic import BaseModel, Field

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


class IntentRouteDecision(BaseModel):
    """Output of `parse_intent_and_route()`.

    rewritten_query: the model's full free-form output — a self-contained
               restatement of the request with reasoning, requirements,
               and success criteria baked in. Becomes `Goal.description`.
    route: quick/react/deep — only populated when `include_routing=True`
               was passed; `None` otherwise.
    clarifying_questions: always empty on this path — the main agent asks
               mid-run via `ask_user_question` when clarification is needed.
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
    "## Open Questions\n"
    "Bullet points for genuine ambiguities worth flagging (omit if none).\n"
)


def _build_intent_prompt(include_routing: bool, capability_block: str, sql_verify_override: str, n_knowledge: int) -> str:
    if not include_routing:
        return _INTENT_INSTRUCTIONS
    return (
        _INTENT_INSTRUCTIONS
        + "\nAfter your restatement, on a NEW line, write ONLY one of the words "
        "`quick`, `react`, or `deep` to classify this request into the "
        "correct execution tier using the rubric below.\n\n"
        + build_tier_rubric(capability_block, sql_verify_override, n_knowledge)
    )


_ROUTE_RE = re.compile(r"\b(quick|react|deep)\b", re.IGNORECASE)


def _extract_route(text: str) -> str | None:
    """Scan the text for the last occurrence of quick/react/deep — "last"
    because the prompt asks for the tier word at the END, and the restatement
    body might coincidentally contain one of the words."""
    matches = _ROUTE_RE.findall(text)
    return matches[-1].lower() if matches else None


def _decision_from_text(text: str, *, include_routing: bool) -> IntentRouteDecision:
    """The model's entire output becomes `rewritten_query`. The only
    extraction is a word scan for `route`."""
    route = _extract_route(text) if include_routing else None
    return IntentRouteDecision(
        rewritten_query=text.strip(),
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
) -> IntentRouteDecision:
    """Single agent run that understands/reorganizes the query and, when
    `include_routing=True`, also picks the execution tier.

    The model's full output is used as the rewritten query (no structured
    parsing). Falls back to the raw query (route='react' when routing was
    requested) if the query is empty or the agent run fails.
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


__all__ = ["IntentRouteDecision", "parse_intent_and_route"]
