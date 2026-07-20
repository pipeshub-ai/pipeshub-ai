from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.agent_loop_lib.hooks.middleware.context import AgentLifecycleContext

if TYPE_CHECKING:
    from app.agent_loop_lib.core.scope import RunScope
    from app.agent_loop_lib.modules.providers.skills.base import SkillMatch
    from app.agent_loop_lib.modules.providers.skills.manager import SkillManager

"""Intent-based skill preloading — the DETERMINISTIC half of the
middleware-vs-tool tradeoff described in the module's design brief (see
`skill_learning.py`'s docstring for the other half of the skills
subsystem). `skill_search`/`load_skill` are tools: the model MAY call
them, but nothing forces it to, so an obviously relevant skill can go
unused simply because the model didn't think to look. This PRE_AGENT
middleware removes that gap by always searching the catalog against the
goal and injecting matches by relevance band BEFORE the first turn:

- score >= `preload_body_threshold`: full skill body injected (the model
  gets the equivalent of having already called `load_skill` for it).
- score >= `mention_threshold` (but below the body threshold): only a
  name + description pointer is injected — enough for the model to
  recognize it's available and call `load_skill`/`skill_search` itself if
  it turns out relevant, without spending prompt budget on every
  medium-relevance skill's full body. Emitted ONLY when the agent this
  pass is about to run for can actually act on it (see
  `_can_load_skills_on_demand` below) — a scoped sub-agent that was never
  granted `load_skill` (e.g. a `domain_agents.py` child whose tool_names
  is an explicit domain-specific allowlist) has no way to follow up on a
  bare pointer, so for one of those this band collapses to "nothing"
  instead: injecting an instruction the model structurally cannot obey is
  worse than injecting nothing at all.

Writes directly to `ctx.scope.extra_prompt_sections["preloaded_skills"]`
(the same dict `Agent.set_prompt_section` writes to, rendered by
`build_system_prompt` every turn — see `agent/prompt.py`) because
`AgentLifecycleContext` carries `scope: RunScope`, not the `Agent` itself,
at PRE_AGENT time.
"""

logger = logging.getLogger(__name__)


def skill_preloading(
    manager: "SkillManager",
    *,
    preload_body_threshold: float = 0.75,
    mention_threshold: float = 0.4,
    top_k: int = 5,
):
    """PRE_AGENT middleware factory. Thresholds are relevance scores from
    `SkillManager.search` (0..1, higher = more relevant) — tune per
    deployment; the defaults favor precision (few, confidently-relevant
    full bodies) over recall, since a wrong full-body injection wastes
    context on every turn of the run, not just once.

    No-ops cleanly (never raises) when the scope is unavailable, the goal
    has no description, the catalog is empty, or nothing clears
    `mention_threshold` — a run should never fail or even warn just
    because preloading found nothing to add.
    """

    async def _middleware(ctx: AgentLifecycleContext, next_fn) -> None:
        if ctx.scope is None or ctx.goal is None or not ctx.goal.description:
            await next_fn()
            return
        if not manager.catalog_snapshot():
            await next_fn()
            return

        try:
            matches = await manager.search(ctx.goal.description, limit=top_k)
        except Exception:
            logger.exception("skill_preloading: search failed, skipping preload")
            await next_fn()
            return

        section = await _render_preload_section(
            manager, matches, session_id=ctx.session_id,
            preload_body_threshold=preload_body_threshold, mention_threshold=mention_threshold,
            emit_pointers=_can_load_skills_on_demand(ctx.scope),
        )
        if section:
            ctx.scope.extra_prompt_sections["preloaded_skills"] = section
        else:
            ctx.scope.extra_prompt_sections.pop("preloaded_skills", None)

        await next_fn()

    return _middleware


def _can_load_skills_on_demand(scope: "RunScope") -> bool:
    """Whether the agent about to run for this PRE_AGENT pass has
    `load_skill` in its own tool_names — i.e. whether a mention-tier
    pointer is something it could actually act on this run.
    `spec.tool_names` of `None` means no explicit allowlist (full/eager
    disclosure of every registered tool), which always includes
    `load_skill` when the skills subsystem is on — only an explicit,
    narrower list that omits it (every `domain_agents.py` child today —
    see `skills_wiring.py`'s "residual grant" comment) withholds it."""
    tool_names = scope.spec.tool_names
    if tool_names is None:
        return True
    return "load_skill" in tool_names


async def _render_preload_section(
    manager: "SkillManager",
    matches: "list[SkillMatch]",
    *,
    session_id: str | None,
    preload_body_threshold: float,
    mention_threshold: float,
    emit_pointers: bool = True,
) -> str:
    bodies: list[str] = []
    pointers: list[str] = []
    for match in matches:
        if match.relevance >= preload_body_threshold:
            try:
                skill = await manager.activate_skill(match.skill.name, session_id=session_id)
            except Exception:
                logger.exception("skill_preloading: failed to activate %r, falling back to pointer", match.skill.name)
                if emit_pointers:
                    pointers.append(f"- {match.skill.name}: {match.skill.description}")
                continue
            bodies.append(f"### Skill: {skill.metadata.name}\n{skill.metadata.description}\n\n{skill.body}")
        elif emit_pointers and match.relevance >= mention_threshold:
            pointers.append(f"- {match.skill.name}: {match.skill.description}")

    if not bodies and not pointers:
        return ""

    parts: list[str] = []
    if bodies:
        parts.append(
            "The following skill(s) look directly relevant to this request and have "
            "already been loaded in full — follow their instructions, no need to call "
            "load_skill for them:\n\n" + "\n\n".join(bodies)
        )
    if pointers:
        parts.append(
            "The following skill(s) may also be relevant — call load_skill(name) if "
            "one turns out to apply:\n" + "\n".join(pointers)
        )
    return "\n\n".join(parts)


__all__ = ["skill_preloading"]
