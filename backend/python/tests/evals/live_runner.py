"""Run the golden cases against a real model.

``live_harness.run_golden_evals`` takes a ``run_agent`` callable and marks every
case skipped without one. This module supplies that callable: it builds a real
``Agent`` on the real runtime, with the real system prompt, gives it the tools
each case grants, and turns the run's result into the ``TraceResult`` the
assertions read.

Only the tools' ``execute`` is stubbed. Their names, descriptions, parameters
and tags are the real ones (see ``tool_cards.py``), because a stub that tells
the model it "changes nothing" cannot test whether the agent asks before it
writes — a write without asking would be the model believing the tool card
rather than misbehaving.

The prompt comes from ``PipesHubPromptBuilder``, the one production builds, so
a change to the prompt or to the rules it assembles reaches this eval. Two
hand-written sentences here would have meant the thing most likely to regress
was the thing never tested.

What this does NOT measure: retrieval quality, citation correctness, or answers
over real customer data. Those need the stack and belong with the integration
and browser tests.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import Tag, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.builtin.planning.task_complete import (
    TaskCompletionOutcome,
)
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.config.constants.ai_models import AzureOpenAILLM
from tests.evals.live_harness import GoldenCase, TraceResult
from tests.evals.tool_cards import ToolCard, card_for, card_from_decorated

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain_core.language_models import BaseChatModel

    from app.agent_loop_lib.core.responses import RunUsage
    from app.agent_loop_lib.core.types import AgentResult
    from app.agents.agent_loop.context import AgentContext

MAX_TURNS = 8

# The terminal tool production always grants. Registering it is what lets the
# agent report a confidence at all: without it AgentResult.confidence is always
# None, and the case that checks confidence can never fail.
FINAL_ANSWER_TOOL = "final_answer"


class StubTool(Tool):
    """A real tool's card with its action replaced.

    Everything the model reads — name, description, parameters, tags — is the
    real tool's. Only ``execute`` is different.
    """

    def __init__(self, card: ToolCard) -> None:
        self._card = card

    @property
    def card(self) -> ToolCard:
        return self._card

    @property
    def name(self) -> str:
        return self._card.name

    @property
    def short_description(self) -> str:
        return self._card.short_description

    @property
    def description(self) -> str:
        return self._card.description

    @property
    def path(self) -> str:
        return self._card.path

    @property
    def tags(self) -> list[Tag]:
        return list(self._card.tags)

    @property
    def parameters(self) -> list[ToolParameter]:
        return list(self._card.parameters)

    async def execute(self, **kwargs: Any) -> ToolOutput:  # noqa: ANN401 - the Tool ABC's own signature
        # Keep what the model passed: a terminal tool's own arguments carry the
        # answer and the confidence, and discarding them would leave nothing to
        # read afterwards.
        return ToolOutput(success=True, data={"result": self._card.result, "arguments": dict(kwargs)})


class TerminalStubTool(StubTool):
    """A stub of a tool whose success ends the run.

    ``execute_tool_call`` only treats a tool as terminal when it is tagged AND
    satisfies the ``TerminalTool`` protocol (``tool_loop.py``). A stub carrying
    the tag without ``extract_outcome`` would let the loop run on past the
    point where production would have stopped — so the case that checks the
    agent asks before it writes could see a write that production never would
    have reached.
    """

    def extract_outcome(
        self, tr: object, call: object, fallback_text: str
    ) -> TaskCompletionOutcome:
        content = getattr(tr, "content", None)
        arguments = content.get("arguments", {}) if isinstance(content, dict) else {}
        answer = ""
        for key in ("answer_markdown", "answer", "user_intent"):
            value = arguments.get(key) if isinstance(arguments, dict) else None
            if isinstance(value, str) and value.strip():
                answer = value
                break
        return TaskCompletionOutcome(
            task_done=True,
            final_output=answer or fallback_text or self._card.result,
        )


class MissingModelError(RuntimeError):
    """No model to run against — a run without one measures nothing."""


class UnknownToolError(RuntimeError):
    """A case grants a tool nothing here can describe faithfully."""


def _ask_user_question_card() -> ToolCard:
    """The real ask_user_question tool's own metadata.

    Its mandatory-ask contract and terminal tag are the whole point of the
    write-gating case; a stub without them would test nothing.
    """
    from app.agents.actions.internal_tools.intrim_tools import InternalTools

    meta = InternalTools.ask_user_question._agent_tool_meta
    return card_from_decorated("internaltools__ask_user_question", meta)


def final_answer_tool() -> Tool:
    """The real ``final_answer`` tool, used as-is.

    Its ``execute`` only packs its own arguments — no I/O — and it implements
    ``extract_outcome``, which is what makes the loop stop and what puts the
    confidence on ``AgentResult``. A stub of it would satisfy neither.
    """
    from app.agent_loop_lib.tools.builtin.planning.final_answer import FinalAnswerTool

    return FinalAnswerTool()


def card_for_tool(tool_name: str, case_id: str | None = None) -> ToolCard:
    """The card a stub of ``tool_name`` should wear, for this case."""
    if tool_name == "internaltools__ask_user_question":
        return _ask_user_question_card()
    if tool_name == FINAL_ANSWER_TOOL:
        raise UnknownToolError(
            "final_answer is used as the real tool, not a stub — see "
            "final_answer_tool() in tests/evals/live_runner.py."
        )
    card = card_for(tool_name, case_id)
    if card is None:
        raise UnknownToolError(
            f"No card for '{tool_name}'. Add one to tests/evals/tool_cards.py "
            "that matches the real tool's description, parameters and tags — a "
            "stub that misdescribes itself makes its case meaningless."
        )
    return card


def registry_for(case: GoldenCase) -> ToolRegistry:
    """The tools a case grants, plus the terminal tool that ends a run.

    ``final_answer`` is the real tool: it is what reports the confidence and
    stops the loop, and it does no I/O. Everything else is a stub wearing the
    real tool's card, terminal ones included so the run stops where production
    would stop.
    """
    registry = ToolRegistry()
    registry.register_tool(final_answer_tool())
    for tool_name in case.granted_tools:
        if tool_name == FINAL_ANSWER_TOOL:
            continue
        card = card_for_tool(tool_name, case.id)
        stub = TerminalStubTool(card) if card.terminal else StubTool(card)
        registry.register_tool(stub)
    return registry


def eval_context(
    *, provider: str, model: str, context_length: int | None = None
) -> AgentContext:
    """The request context a case runs in, with this model stamped on it.

    Separate from prompt building so the tier a model lands in can be checked
    without the retrieval stack installed; the prompt builder needs it, this
    does not.
    """
    from unittest.mock import MagicMock

    from app.agents.agent_loop.context import AgentContext

    ctx = AgentContext(
        org_id="eval-org",
        user_id="eval-user",
        user_email="eval@pipeshub.test",
        user_info={"userId": "eval-user", "orgId": "eval-org"},
        org_info={"name": "Eval Org"},
        logger=MagicMock(),
        llm_provider=provider,
        model_name=model,
        context_length=context_length,
        retrieval_service=MagicMock(),
        graph_provider=MagicMock(),
        config_service=MagicMock(),
        has_knowledge=True,
        agent_knowledge=[
            {"displayName": "KB", "name": "KB", "_id": "kb1", "connectorId": "kb1", "type": "KB"}
        ],
    )
    ctx.tool_state.update(
        {
            "agent_knowledge": ctx.agent_knowledge or [],
            "has_knowledge": True,
            "available_connectors": [],
            "agent_toolsets": [],
        }
    )
    return ctx


def build_system_prompt(
    tool_names: list[str],
    *,
    provider: str,
    model: str,
    context_length: int | None = None,
) -> str:
    """The prompt production would build for these tools and this model.

    The provider and model are stamped onto the context because the prompt
    depends on them: `model_profile` decides whether worked example traces are
    injected, and leaving them blank makes every run look like a mid-tier model
    and adds examples the scheduled model would never see. An eval whose prompt
    differs from production's measures the wrong prompt.

    ``context_length`` is the window an admin configured for the model. Left
    unset it matches production's own fallback for a model nobody filled in.

    Imported here rather than at module load: the builder pulls in the
    retrieval stack, which a machine running only the offline tests need not
    have installed.
    """
    from app.agents.agent_loop.prompt_builder import PipesHubPromptBuilder

    ctx = eval_context(provider=provider, model=model, context_length=context_length)
    spec = AgentSpec(
        name="answer-quality-eval",
        system_prompt="BASE_REACT_PROMPT",
        tool_names=list(tool_names),
        model=ModelSpec(provider="langchain", model="eval"),
    )
    return PipesHubPromptBuilder(ctx).build(
        spec, AgentRuntime(), Goal(description="q"), [], {}
    )


def build_chat_model(provider: str, model: str, api_key: str | None) -> BaseChatModel:
    """A LangChain chat model for ``provider``."""
    if not api_key:
        raise MissingModelError(
            f"No API key for '{provider}'. Set the key in the workflow's "
            "environment (TEST_AZURE_OPENAI_API_KEY for Azure OpenAI, "
            "TEST_OPENAI_API_KEY for OpenAI) and run again."
        )
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, api_key=api_key, temperature=0)
    if provider == "azure_openai":
        from langchain_openai import AzureChatOpenAI

        endpoint = os.getenv("TEST_AZURE_OPENAI_ENDPOINT")
        deployment = os.getenv("TEST_AZURE_OPENAI_DEPLOYMENT_NAME")
        if not endpoint or not deployment:
            raise MissingModelError(
                "Azure OpenAI needs an endpoint and a deployment as well as a "
                "key. Set TEST_AZURE_OPENAI_ENDPOINT and "
                "TEST_AZURE_OPENAI_DEPLOYMENT_NAME and run again."
            )
        return AzureChatOpenAI(
            model=model,
            api_key=api_key,
            azure_endpoint=endpoint,
            azure_deployment=deployment,
            # The product's own version, so the evals talk to Azure the way the
            # thing they are measuring does.
            api_version=AzureOpenAILLM.AZURE_OPENAI_VERSION.value,
            temperature=0,
        )
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, api_key=api_key, temperature=0)
    raise MissingModelError(
        f"'{provider}' is not a provider this runner knows. Use 'azure_openai', "
        "'openai' or 'anthropic', or add it to build_chat_model in "
        "tests/evals/live_runner.py."
    )


def _model_confidence(result: AgentResult) -> str | None:
    """What the agent claimed, in the product's own spelling.

    ``AgentResult.confidence`` is a ``Confidence`` enum ("very_high"); the
    assertions compare against the product's labels ("Very High"). Handing over
    the enum value would make every comparison miss and the case pass whatever
    the agent claimed. When the final-answer tool is off — the default —
    production reads the level off the answer text instead, so this falls back
    to the same parser.
    """
    from app.agents.agent_loop.confidence import normalize

    level = getattr(result, "confidence", None)
    raw = getattr(level, "value", level)
    if raw is None:
        from app.utils.streaming import parse_confidence_from_answer

        output = getattr(result, "output", "") or ""
        if isinstance(output, str):
            _, raw = parse_confidence_from_answer(output)
    return normalize(raw)


def trace_from(result: AgentResult, case: GoldenCase) -> TraceResult:
    """The agent's run, in the shape the golden assertions read."""
    from app.agents.agent_loop.confidence import reconcile

    tool_calls: list[str] = []
    for turn in getattr(result, "turns", []) or []:
        for call in getattr(turn, "tool_calls", []) or []:
            name = getattr(call, "name", None)
            if name:
                tool_calls.append(name)
    output = getattr(result, "output", "")
    claimed = _model_confidence(result)
    unavailable = list(unavailable_sources_for(case))
    # What production would show the user: an optimistic level is capped when a
    # source was missing.
    shown = reconcile(
        claimed,
        unavailable_sources=unavailable,
        citation_count=0,
    )
    return TraceResult(
        first_tool=tool_calls[0] if tool_calls else None,
        tool_calls=tool_calls,
        final_answer=output if isinstance(output, str) else str(output or ""),
        # The level the agent CLAIMED, not the capped one. Asserting on the
        # capped value would mean the case could only fail if the cap itself
        # broke — the agent over-claiming, which is the behaviour change worth
        # catching, would be hidden by the very safety net this eval is not
        # testing. The capped value is recorded below so a run still shows what
        # a user would have seen.
        confidence=claimed,
        unavailable_sources=tuple(unavailable),
        completion_data={
            "model_confidence": claimed,
            "shown_confidence": shown,
            "unavailable_sources": unavailable,
            "record_ids": list(getattr(result, "record_ids", []) or []),
            "needs_input": getattr(result, "needs_input", None),
            "success": getattr(result, "success", None),
            "error": getattr(result, "error", None),
        },
    )


def unavailable_sources_for(case: GoldenCase) -> tuple[str, ...]:
    """Sources a case says were unreachable during the run.

    Only the confidence case has any today; it is read from the case rather
    than hard-coded so a new case declaring one behaves the same way.
    """
    for card_name in case.granted_tools:
        card = card_for(card_name, case.id)
        if card and card.sources_unavailable:
            return card.sources_unavailable
    return ()


class UsageTally:
    """Tokens across every case in a run, for costing."""

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.requests = 0

    def add(self, usage: RunUsage | None) -> None:
        self.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
        self.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
        self.requests += int(getattr(usage, "requests", 0) or 0)


class BadContextLengthError(ValueError):
    """EVAL_CONTEXT_LENGTH was set to something that is not a window size."""


def context_length_from_env() -> int | None:
    """The model's context window, when CI was told what it is.

    Unset means the same thing it means in the product: nobody filled it in,
    so the conservative fallback applies and the model is treated as a small
    one.
    """
    raw = os.getenv("EVAL_CONTEXT_LENGTH", "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise BadContextLengthError(
            f"EVAL_CONTEXT_LENGTH is set to {raw!r}. It should be the model's "
            "context window in tokens, for example 128000, or left unset."
        ) from None
    if value <= 0:
        raise BadContextLengthError(
            f"EVAL_CONTEXT_LENGTH is set to {value}. A context window has to be "
            "a positive number of tokens, or left unset."
        )
    return value


def prompt_tier(provider: str, model: str, context_length: int | None = None) -> str:
    """Which prompt this model gets — the runs of different tiers are not alike.

    Recorded with the run so a reader can tell whether last night's numbers and
    tonight's were measured on the same prompt.
    """
    from app.agents.agent_loop.model_tier import ModelProfile

    return str(
        ModelProfile.resolve(
            provider=provider,
            model_name=model,
            context_length=context_length,
            is_reasoning=False,
        ).tier
    )


def make_run_agent(
    chat_model: BaseChatModel,
    model_name: str,
    tally: UsageTally,
    *,
    provider: str,
    context_length: int | None = None,
) -> Callable[[GoldenCase, object], Awaitable[TraceResult]]:
    """The ``run_agent`` callable ``run_golden_evals`` expects."""

    async def run_agent(case: GoldenCase, _model: object) -> TraceResult:
        from app.agents.agent_loop.langchain_transport import LangChainTransport

        transport_registry = TransportRegistry()
        transport_registry.register(
            "langchain",
            lambda: LangChainTransport(chat_model, model_name=model_name),
        )
        registry = registry_for(case)
        runtime = AgentRuntime(
            transport_registry=transport_registry, tool_registry=registry
        )
        spec = AgentSpec(
            name="answer-quality-eval",
            system_prompt=build_system_prompt(
                registry.names(),
                provider=provider,
                model=model_name,
                context_length=context_length,
            ),
            tool_names=registry.names(),
            model=ModelSpec(provider="langchain", model=model_name),
            loop=ReActLoop(),
            max_turns=MAX_TURNS,
        )
        agent = Agent(spec, runtime)
        result = await agent.run(Goal(description=case.query))
        tally.add(getattr(result, "usage", None))
        return trace_from(result, case)

    return run_agent


def resolve_model(
    provider_override: str | None = None, model_override: str | None = None
) -> tuple[str, str, str | None]:
    """Provider, model and key — the key chosen for the FINAL provider.

    The provider must be settled before its key is read. Picking the key first
    and then letting a command-line flag change the provider would send one
    provider's key to another's endpoint: an authentication failure, and a
    secret handed to a party that should never see it.

    Reads the variables the integration workflows already set, so a scheduled
    run needs no new secret.
    """
    provider = provider_override or os.getenv("EVAL_PROVIDER", "openai")
    model = model_override or os.getenv("EVAL_MODEL") or ""
    if provider == "azure_openai":
        return (
            provider,
            model
            or os.getenv("TEST_AZURE_OPENAI_MODEL")
            or os.getenv("TEST_AZURE_OPENAI_DEPLOYMENT_NAME")
            or "",
            os.getenv("TEST_AZURE_OPENAI_API_KEY"),
        )
    if provider == "openai":
        return provider, model or os.getenv("TEST_OPENAI_LLM_MODEL") or "gpt-4o-mini", os.getenv(
            "TEST_OPENAI_API_KEY"
        )
    if provider == "anthropic":
        return provider, model or "claude-haiku-4-5", os.getenv("TEST_ANTHROPIC_API_KEY")
    # An unknown provider has no key here; build_chat_model says so by name
    # rather than trying whatever key happens to be set.
    return provider, model, None


__all__ = [
    "BadContextLengthError",
    "FINAL_ANSWER_TOOL",
    "MAX_TURNS",
    "MissingModelError",
    "StubTool",
    "TerminalStubTool",
    "UnknownToolError",
    "UsageTally",
    "build_chat_model",
    "build_system_prompt",
    "card_for_tool",
    "context_length_from_env",
    "eval_context",
    "final_answer_tool",
    "make_run_agent",
    "prompt_tier",
    "registry_for",
    "resolve_model",
    "trace_from",
    "unavailable_sources_for",
]
