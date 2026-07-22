"""Declarative chat-mode catalog: the single place that says which
`LoopStrategy` kind each `chatMode` wire value runs under and whether it
composes domain agents (`domain_agents.py`).

Before this module, `router.py` special-cased mode NAMES directly (`if
route == "quick": ... PlanExecuteLoop ...`) and `factory.py` inferred
composition/tool-grant behavior from `isinstance(loop, OrchestratorLoop)`
— two places a future mode change (e.g. "make deep also skip
composition", "add a new tier") had to touch, in sync, with no single
source of truth for the mapping. `MODE_CATALOG` below IS that source of
truth: changing a mode's loop kind or composition flag is a one-line
edit here, and both `router.py` (loop construction) and `factory.py`
(composition/tool-grant gating) read the SAME `ModeDefinition`.

Current catalog (see the "Chat Mode Redesign" plan):

- `quick` -> flat ReAct, no planner, no domain-agent composition, AND no
  intent-understanding pre-step (`skip_intent=True`) — the fast path
  skips `intent.parse_intent_and_route()`'s LLM round-trip entirely and
  runs the raw user query as `Goal.description` verbatim, instead of
  paying for an upfront plan, delegate indirection, OR a query-rewrite
  call before the agent even starts.
- `react` -> ReAct + domain agents (the auto-router's default tier).
- `planExecute` (alias `verification`, the pre-existing wire value from
  before this rename) -> `PhaseDriver`-backed `PlanCritiqueExecuteLoop`
  (plans/critiques/verifies via the `create_plan`/`critique_plan`/
  `verify_result`/`replan` TOOLS on the executing agent itself, no
  spawning — see `loops/plan_execute.py`) + domain agents — this is what
  `quick` used to mean.
- `deep` -> `OrchestratorLoop`, ALWAYS composes domain agents: the
  top-level orchestrator itself still only ever sees the four
  coordination tools (`create_plan`/`critique_plan`/`spawn_agent`/
  `verify_result` — never gated by `compose_domain_agents`), but its
  `spawn_agent` pool becomes the composed domain-agent delegates plus
  residual tools instead of the raw flat tool list (see `factory.py`
  and `loops/orchestrator.py::_domain_overview`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

LoopKind = Literal["react", "plan_execute", "orchestrator"]

__all__ = ["MODE_CATALOG", "LoopKind", "ModeDefinition", "resolve_mode"]


@dataclass(frozen=True)
class ModeDefinition:
    """One row of the catalog. Pure data — no loop/tool construction lives
    here (that needs an LLM/tool registry/runtime this module has no
    business depending on); see `router.py`'s `_build_loop()` (keyed off
    `loop_kind`, never off `name`) and `factory.py`'s composition gate
    (keyed off `compose_domain_agents`)."""

    name: str
    """Canonical wire value (`chatMode`), e.g. `"quick"`."""

    loop_kind: LoopKind = "react"
    """Which `LoopStrategy` family this mode runs under."""

    compose_domain_agents: bool = True
    """Whether `PipesHubAgentFactory.create()` should narrow this mode's
    top-level tool grant to composed domain-agent delegates + residual
    tools (subject to the `PIPESHUB_USE_COMPOSED_AGENTS` kill-switch).
    For `loop_kind == "orchestrator"`, this instead gates whether
    `register_domain_agents()` runs to build the SPAWN POOL — the
    top-level orchestrator's own grant is always just the four
    coordination tools regardless of this flag."""

    aliases: tuple[str, ...] = ()
    """Legacy/alternate wire values that resolve to this same mode, e.g.
    `planExecute`'s `("verification",)` so old conversations/clients that
    still send `chatMode=verification` keep working unchanged."""

    skip_intent: bool = False
    """When True, `router.select_loop_and_goal()` skips the
    `intent.parse_intent_and_route()` LLM call entirely for this mode and
    builds `Goal` straight from the raw query (see that function). Only
    meaningful for modes resolved WITHOUT the tier classifier (i.e. never
    consulted for `chat_mode == "auto"`, since that path always needs one
    intent call to pick the tier in the first place) — a mode that wants
    the absolute minimum per-request LLM calls, at the cost of no query
    rewriting/follow-up resolution/requirements extraction."""


MODE_CATALOG: tuple[ModeDefinition, ...] = (
    ModeDefinition(name="quick", loop_kind="react", compose_domain_agents=False, skip_intent=True),
    ModeDefinition(name="react", loop_kind="react", compose_domain_agents=True),
    ModeDefinition(
        name="planExecute", loop_kind="plan_execute", compose_domain_agents=True,
        aliases=("verification",),
    ),
    ModeDefinition(name="deep", loop_kind="orchestrator", compose_domain_agents=True),
)

_BY_KEY: dict[str, ModeDefinition] = {}
for _mode in MODE_CATALOG:
    _BY_KEY[_mode.name.lower()] = _mode
    for _alias in _mode.aliases:
        _BY_KEY[_alias.lower()] = _mode


def resolve_mode(chat_mode: str | None) -> ModeDefinition | None:
    """Resolves a raw `chatMode` wire value (canonical name or legacy
    alias, case-insensitive) to its `ModeDefinition`. Returns `None` for
    `"auto"`, an empty value, or anything else unrecognized — callers
    treat `None` as "run the LLM tier classifier and resolve again off
    its `quick`/`react`/`deep` verdict" (see `router.py`)."""
    if not chat_mode:
        return None
    return _BY_KEY.get(chat_mode.strip().lower())
