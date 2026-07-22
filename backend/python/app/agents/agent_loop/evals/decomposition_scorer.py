"""Deterministic, offline scoring of a `Plan` against a `DecompositionEvalQuery`
— no model call, no network. Complements (never replaces) `PlanCritic`'s
LLM-judged overlap/gap check (`modules/pipeline/critic/plan_critic.py`),
which the harness runs SEPARATELY over the same plan for the checks a
keyword heuristic cannot make (actual semantic overlap between two steps'
descriptions). This module only checks the mechanical, rule-based
invariants a query's structural expectations pin down: step count in
range, expected domains touched, dependency present when the query
requires one, and every multi-step plan's steps carrying `boundaries` per
the Phase 1 planning instructions' own requirement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent_loop_lib.modules.pipeline.planner.base import Plan
    from app.agents.agent_loop.evals.decomposition_queries import DecompositionEvalQuery

# Keyword -> substrings that count as evidence a step touches that domain,
# checked against `step.domain`, `step.tool_names`, and `step.description`
# (lowercased). Deliberately loose: the model is never forced to spell a
# domain exactly like `DomainAgentDefinition.name` (see that catalog,
# `domain_agents.py`) — `domain` is free text the model writes for a
# human/critique to read, not a validated enum.
DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "web": ("web", "internet", "public"),
    "coding": ("coding", "code", "sandbox", "csv", "pdf", "spreadsheet", "file", "docx", "xlsx"),
    "internal": ("internal", "knowledge", "jira", "confluence", "retrieval", "wiki", "confidential"),
    "calculator": ("calculat", "arithmetic", "math"),
    "calendar": ("calendar", "schedule", "meeting", "availability"),
}


@dataclass
class ScoreIssue:
    severity: str  # "error" | "warning"
    message: str


@dataclass
class DecompositionScore:
    query_id: str
    passed: bool
    step_count: int
    issues: list[ScoreIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ScoreIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ScoreIssue]:
        return [i for i in self.issues if i.severity == "warning"]


def _step_text(step: object) -> str:
    """Everything about one `PlanStep` worth keyword-matching, lowercased
    and joined — `domain`/`tool_names`/`description` all count as
    evidence the step touches a given area of work."""
    domain = getattr(step, "domain", "") or ""
    tool_names = " ".join(getattr(step, "tool_names", None) or [])
    description = getattr(step, "description", "") or ""
    return f"{domain} {tool_names} {description}".lower()


def _domains_touched(steps: list[object]) -> set[str]:
    texts = [_step_text(s) for s in steps]
    touched: set[str] = set()
    for domain, keywords in DOMAIN_KEYWORDS.items():
        if any(any(kw in text for kw in keywords) for text in texts):
            touched.add(domain)
    return touched


def score_plan(plan: "Plan | None", query: "DecompositionEvalQuery") -> DecompositionScore:
    """Score one produced `Plan` against the query's structural
    expectations. `plan is None` or a plan with no structured `steps`
    (the model never called `create_plan` with a `steps` array, or ran out
    of turns before doing so) is always a hard failure — there is nothing
    to check structurally, and for every query in this dataset SOME
    decision (even "zero/one step, answer directly") is expected."""
    if plan is None or plan.steps is None:
        return DecompositionScore(
            query_id=query.id, passed=False, step_count=0,
            issues=[ScoreIssue("error", "create_plan was never called with a structured `steps` array.")],
        )

    steps = plan.steps
    step_count = len(steps)
    issues: list[ScoreIssue] = []

    if step_count < query.min_steps or step_count > query.max_steps:
        issues.append(ScoreIssue(
            "error",
            f"step count {step_count} outside expected range "
            f"[{query.min_steps}, {query.max_steps}] for this query.",
        ))

    if query.expected_domains:
        touched = _domains_touched(steps)
        missing = query.expected_domains - touched
        if missing:
            issues.append(ScoreIssue(
                "error",
                f"expected domain(s) {sorted(missing)} not evidenced by any step "
                f"(touched: {sorted(touched)}).",
            ))

    has_dependency = any(step.depends_on for step in steps)
    if query.requires_dependency and not has_dependency:
        issues.append(ScoreIssue(
            "error",
            "query has a genuine data dependency between its parts, but no step "
            "sets `depends_on` — a dependent step will run without its prerequisite's output.",
        ))

    if step_count > 1:
        missing_boundaries = [s.id for s in steps if not s.boundaries]
        if missing_boundaries:
            issues.append(ScoreIssue(
                "warning",
                f"multi-step plan has step(s) with no `boundaries`: {missing_boundaries} — "
                "scope overlap with a sibling step is easy to miss without them.",
            ))

    passed = not any(i.severity == "error" for i in issues)
    return DecompositionScore(query_id=query.id, passed=passed, step_count=step_count, issues=issues)


__all__ = ["DOMAIN_KEYWORDS", "DecompositionScore", "ScoreIssue", "score_plan"]
