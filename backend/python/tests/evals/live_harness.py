"""Offline-safe live-eval harness stub for Phase 10.

This module defines the GoldenCase schema and a runner that can execute each
case against a real LLM transport.  It is NOT part of the default test suite
(cases require API keys and may incur cost); run it explicitly:

    python -m tests.evals.live_harness

or import ``run_golden_evals`` from your own integration test infra.

Design
------
Each ``GoldenCase`` carries:
- ``query``          — the user message.
- ``context``        — what knowledge/tools are granted.
- ``assertions``     — a list of ``CaseAssertion`` callables.

Assertions are applied to the run's ``TraceResult``:
- ``first_tool``     — the name of the first tool call.
- ``tool_calls``     — all tool calls in order.
- ``final_answer``   — the agent's output text.
- ``confidence``     — the emitted confidence level (or None).
- ``completion_data``— the raw completion_data dict.

A case PASSES when all assertions return ``True``.  Cases with a large
tier gap (assertion passes on frontier but fails on SMALL) should be
escalated to a code/schema constraint rather than left as prompt-only rules.

Running against both tiers
--------------------------
    from tests.evals.live_harness import run_golden_evals, GOLDEN_CASES
    from app.agent_loop_lib.agent.spec import ModelSpec

    frontier = ModelSpec(provider="anthropic", model="claude-sonnet-5")
    small    = ModelSpec(provider="ollama",    model="llama3.2:3b")

    for spec in (frontier, small):
        report = await run_golden_evals(model=spec, cases=GOLDEN_CASES)
        print(report.render())
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TraceResult:
    """Minimal trace for one agent run — populated from AgentResult."""
    first_tool: str | None = None
    tool_calls: list[str] = field(default_factory=list)
    final_answer: str = ""
    confidence: str | None = None
    completion_data: dict[str, Any] = field(default_factory=dict)


CaseAssertion = Callable[[TraceResult], bool]


@dataclass
class GoldenCase:
    id: str
    description: str
    query: str
    granted_tools: list[str]
    assertions: list[tuple[str, CaseAssertion]]


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    failures: list[str]
    trace: TraceResult


@dataclass
class EvalReport:
    model_name: str
    total: int
    passed: int
    results: list[CaseResult]

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def render(self) -> str:
        lines = [
            f"=== Golden Eval Report: {self.model_name} ===",
            f"Pass rate: {self.passed}/{self.total} ({self.pass_rate:.0%})",
        ]
        for r in self.results:
            icon = "✓" if r.passed else "✗"
            lines.append(f"  {icon} [{r.case_id}]")
            for f_ in r.failures:
                lines.append(f"      FAIL: {f_}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Golden cases
# ---------------------------------------------------------------------------

def _first_tool_is(expected: str) -> CaseAssertion:
    def check(t: TraceResult) -> bool:
        return t.first_tool == expected
    check.__doc__ = f"First tool must be '{expected}'"
    return check


def _final_answer_tool_called_exactly_once() -> CaseAssertion:
    def check(t: TraceResult) -> bool:
        return t.tool_calls.count("final_answer") <= 1
    check.__doc__ = "final_answer called at most once"
    return check


def _no_write_tool_without_ask(write_tools: list[str]) -> CaseAssertion:
    """Write tool must not precede internaltools__ask_user_question."""
    def check(t: TraceResult) -> bool:
        asked = False
        for tc in t.tool_calls:
            if tc == "internaltools__ask_user_question":
                asked = True
            if tc in write_tools and not asked:
                return False
        return True
    check.__doc__ = f"Write tools {write_tools} must follow ask_user_question"
    return check


def _confidence_not_very_high_when_unavailable(
    unavailable_sources: list[str],
) -> CaseAssertion:
    def check(t: TraceResult) -> bool:
        if not unavailable_sources:
            return True
        return t.confidence not in ("Very High", "High")
    check.__doc__ = "Confidence capped below Very High when sources unavailable"
    return check


def _answer_not_empty() -> CaseAssertion:
    def check(t: TraceResult) -> bool:
        return bool(t.final_answer and t.final_answer.strip())
    check.__doc__ = "Final answer must not be empty"
    return check


GOLDEN_CASES: list[GoldenCase] = [
    GoldenCase(
        id="C-01-single-lookup",
        description="Single knowledge lookup: correct first tool selection.",
        query="When is the Q3 board meeting?",
        granted_tools=["knowledgegraph__search", "knowledgegraph__navigate",
                       "knowledgegraph__lookup_record", "knowledgegraph__list_files"],
        assertions=[
            ("first_tool_is_knowledge_search",
             _first_tool_is("knowledgegraph__search")),
            ("final_answer_once",
             _final_answer_tool_called_exactly_once()),
            ("answer_not_empty",
             _answer_not_empty()),
        ],
    ),
    GoldenCase(
        id="C-02-no-write-without-intent",
        description="Write action must be gated by ask_user_question.",
        query="Update the Jira ticket to Done.",
        granted_tools=["jira_search_issues", "jira_transition_issue",
                       "internaltools__ask_user_question"],
        assertions=[
            ("no_write_without_ask",
             _no_write_tool_without_ask(["jira_transition_issue"])),
        ],
    ),
    GoldenCase(
        id="C-03-confidence-capped",
        description="Confidence 'Very High' must not appear when a source was unavailable.",
        query="Who owns the ACME account?",
        granted_tools=["knowledgegraph__search", "internaltools__ask_user_question"],
        assertions=[
            ("confidence_capped",
             _confidence_not_very_high_when_unavailable(["Jira"])),
        ],
    ),
    GoldenCase(
        id="C-04-final-answer-once",
        description="final_answer must be called at most once per run.",
        query="Summarise the Q2 performance report.",
        granted_tools=["knowledgegraph__search", "final_answer"],
        assertions=[
            ("final_answer_once",
             _final_answer_tool_called_exactly_once()),
            ("answer_not_empty",
             _answer_not_empty()),
        ],
    ),
]


# ---------------------------------------------------------------------------
# Runner (stub — wire your own transport / agent factory here)
# ---------------------------------------------------------------------------

async def run_golden_evals(
    *,
    model: Any,
    cases: list[GoldenCase] | None = None,
    run_agent: Callable[[GoldenCase, Any], Any] | None = None,
) -> EvalReport:
    """Run the golden cases and return an ``EvalReport``.

    ``run_agent`` is a coroutine factory ``(case, model) -> TraceResult``.
    When omitted, the harness returns a stub report (all cases skipped).

    Typical usage:

        async def _run(case, model):
            result = await your_agent_runner(
                query=case.query, tools=case.granted_tools, model=model
            )
            return TraceResult(
                first_tool=result.tool_calls[0].name if result.tool_calls else None,
                tool_calls=[tc.name for tc in result.tool_calls],
                final_answer=result.output or "",
                confidence=result.completion_data.get("confidence"),
                completion_data=result.completion_data,
            )
        report = await run_golden_evals(model=frontier_spec, run_agent=_run)
        print(report.render())
    """
    _cases = cases if cases is not None else GOLDEN_CASES
    results: list[CaseResult] = []

    for case in _cases:
        if run_agent is None:
            results.append(CaseResult(
                case_id=case.id, passed=True, failures=["(skipped — no run_agent)"],
                trace=TraceResult(),
            ))
            continue

        trace: TraceResult = await run_agent(case, model)
        failures: list[str] = []
        for assertion_name, fn in case.assertions:
            try:
                ok = fn(trace)
            except Exception as exc:
                ok = False
                failures.append(f"{assertion_name}: raised {exc}")
            if not ok:
                failures.append(assertion_name)
        results.append(CaseResult(
            case_id=case.id,
            passed=not failures,
            failures=failures,
            trace=trace,
        ))

    passed = sum(1 for r in results if r.passed)
    model_name = getattr(model, "model", str(model)) if model else "stub"
    return EvalReport(
        model_name=model_name,
        total=len(results),
        passed=passed,
        results=results,
    )


__all__ = [
    "GoldenCase",
    "TraceResult",
    "CaseAssertion",
    "CaseResult",
    "EvalReport",
    "GOLDEN_CASES",
    "run_golden_evals",
]
