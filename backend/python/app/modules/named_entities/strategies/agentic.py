"""Bounded read-only extraction agent. Accepted spans survive every stop."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.context import CancellationToken
from app.agent_loop_lib.core.exceptions import BudgetExceeded
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.modules.providers.budget.tracker import BudgetTracker
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.modules.named_entities.domain.config import NamedEntityBudgets
from app.modules.named_entities.mentions import RawMention
from app.modules.named_entities.strategies.agent.prompt import (
    STATIC_PREFIX,
    goal_message,
)
from app.modules.named_entities.strategies.agent.tools import (
    ExtractionSession,
    build_tools,
)
from app.modules.named_entities.strategies.agent.transport import SlottedTransport
from app.modules.named_entities.strategies.base import (
    ExtractionContext,
    ExtractionDocument,
    StrategyResult,
)

logger = logging.getLogger(__name__)

_WINDOW = 5


def _summary(seed: list[RawMention]) -> str:
    counts: dict[str, int] = {}
    for mention in seed:
        counts[mention.kind.value] = counts.get(mention.kind.value, 0) + 1
    if not counts:
        return "Already captured: nothing. Add whatever the blocks contain."
    parts = ", ".join(f"{n} {kind}" for kind, n in sorted(counts.items()))
    return f"Already captured: {parts}. Do not resubmit those; you may add missed ones."


# Turns a run may spend on submits that only came back rejected.
_FEEDBACK_TURNS = 2

class AgenticExtractionStrategy:
    name = "agent"

    async def extract(
        self, doc: ExtractionDocument, seed: list[RawMention], ctx: ExtractionContext
    ) -> StrategyResult:
        budgets: NamedEntityBudgets = ctx.budgets  # type: ignore[assignment]
        session = ExtractionSession(
            doc.units, enabled=ctx.enabled, norm_ctx=ctx.norm, seed_count=len(seed)
        )
        first = doc.units[:_WINDOW]
        for unit in first:
            session.read.add(unit.block_index)
        tools = build_tools(session)
        registry = ToolRegistry()
        for tool in tools:
            registry.register_tool(tool)

        transport = ctx.transport
        if transport is None:
            return StrategyResult(
                mentions=[],
                termination_reason="llm_error",
                failed_before_first_turn=True,
            )
        if not isinstance(transport, SlottedTransport):
            transport = SlottedTransport(transport)

        transports = TransportRegistry()
        transports.register("indexing_slotted", lambda: transport)
        token = CancellationToken()
        stop: dict[str, str | None] = {"reason": None}

        def _install(hooks) -> None:
            state = {"streak": 0, "staged": 0, "read": 0, "rejected": 0, "feedback": 0}

            async def _pre(hook_ctx, next_fn) -> None:
                state["staged"] = len(session.staging)
                state["read"] = len(session.read)
                state["rejected"] = session.staging.rejected
                await next_fn()

            async def _post(hook_ctx, next_fn) -> None:
                # Reading unseen blocks is progress; re-reading is not. A submit that only
                # came back rejected told the model what to fix, so it earns two more turns.
                grew = len(session.staging) > state["staged"] or len(session.read) > state["read"]
                rejected = session.staging.rejected > state["rejected"]
                corrected = not grew and rejected and state["feedback"] < _FEEDBACK_TURNS
                if grew:
                    state["feedback"] = 0
                elif rejected:
                    state["feedback"] += 1
                progressed = grew or corrected
                if progressed or session.finished:
                    state["streak"] = 0
                else:
                    state["streak"] += 1
                    if state["streak"] >= 2:
                        stop["reason"] = "no_progress"
                        token.cancel()
                await next_fn()

            hooks.on(HookEvent.PRE_TURN).use(_pre)
            hooks.on(HookEvent.POST_TURN).use(_post)

        runtime = AgentRuntime(
            transport_registry=transports,
            tool_registry=registry,
            budget=BudgetTracker(
                # Counted across turns, each of which resends the prompt, tool
                # schemas and history, so a cap scaled to document size alone
                # can block the second turn of a short document.
                max_input_tokens=budgets.max_input_tokens,
                max_output_tokens=budgets.max_output_tokens,
                max_tool_calls=budgets.max_tool_calls,
                max_turns=budgets.max_turns,
            ),
            cancellation_token=token,
            event_emitter=None,
        )
        spec = AgentSpec(
            name="ner-extractor",
            system_prompt=STATIC_PREFIX,
            tool_names=[tool.name for tool in tools],
            model=ModelSpec(provider="indexing_slotted", model=ctx.model_name or "indexing"),
            loop=ReActLoop(),
            max_turns=budgets.max_turns,
            middleware=[_install],
        )
        windows = max(1, (len(doc.units) + _WINDOW - 1) // _WINDOW) if doc.units else 0
        goal = Goal(
            description=goal_message(
                record_name=doc.record_name,
                record_type=doc.record_type,
                reference=_reference_label(ctx.norm.reference_time_ms, ctx.norm.tz),
                kinds=sorted(kind.value for kind in ctx.enabled),
                spotlight=session.spotlight(first),
                deterministic_summary=_summary(seed),
                window_count=windows,
            )
        )
        try:
            result = await asyncio.wait_for(agent_run(spec, runtime, goal), timeout=budgets.wall_clock_seconds)
        except TimeoutError:
            return _kept(session, "timeout")
        except BudgetExceeded as exc:
            return _kept(session, _budget_reason(str(exc)) or "budget_tokens")
        except Exception as exc:
            logger.warning("Named-entity agent failed before producing a result: %s", type(exc).__name__)
            failed_early = len(session.staging) == 0
            return StrategyResult(
                mentions=session.staging.mentions(),
                termination_reason="llm_error",
                submitted=session.staging.submitted,
                rejected=session.staging.rejected,
                failed_before_first_turn=failed_early,
                tools_unsupported="bind_tools" in str(exc),
            )
        reason = _reason(result, stop["reason"])
        if reason == "finish_ok" and not session.finished and session.unread():
            # A plain-text reply or the turn cap ended the run with blocks never read.
            reason = "budget_turns"
        usage = getattr(result, "usage", None)
        turns = getattr(result, "turns", []) or []
        tool_calls = sum(len(getattr(turn, "tool_calls", None) or []) for turn in turns)
        return StrategyResult(
            mentions=session.staging.mentions(),
            termination_reason=reason,
            turns=len(turns),
            tool_calls=tool_calls,
            tokens_in=getattr(usage, "input_tokens", 0) or 0,
            tokens_out=getattr(usage, "output_tokens", 0) or 0,
            submitted=session.staging.submitted,
            rejected=session.staging.rejected,
            rejected_reasons=dict(session.staging.rejected_reasons),
            incomplete=bool(session.unread()),
            failed_before_first_turn=reason == "llm_error" and not turns,
            tools_unsupported=reason == "llm_error" and "bind_tools" in (getattr(result, "error", None) or ""),
        )


async def agent_run(spec, runtime, goal):
    return await Agent(spec, runtime).run(goal)


def _kept(session: ExtractionSession, reason: str) -> StrategyResult:
    return StrategyResult(
        mentions=session.staging.mentions(),
        termination_reason=reason,  # type: ignore[arg-type]
        submitted=session.staging.submitted,
        rejected=session.staging.rejected,
        rejected_reasons=dict(session.staging.rejected_reasons),
        incomplete=bool(session.unread()),
    )


def _reason(result, forced: str | None) -> str:
    if forced:
        return forced
    if getattr(result, "cancelled", False):
        return "no_progress"
    budget = _budget_reason(getattr(result, "error", None) or "")
    if budget:
        return budget
    if getattr(result, "success", False):
        return "finish_ok"
    return "llm_error"


def _budget_reason(message: str) -> str | None:
    if "max_turns" in message or "max_tool_calls" in message:
        return "budget_turns"
    if "max_input" in message or "max_output" in message or "max_cost" in message:
        return "budget_tokens"
    return None


def _reference_label(reference_time_ms: int | None, tz: str) -> str:
    if not reference_time_ms:
        return "unknown"
    try:
        zone = ZoneInfo(tz)
    except Exception:
        zone = UTC
    return datetime.fromtimestamp(reference_time_ms / 1000, tz=zone).strftime("%Y-%m-%d (%A)")
