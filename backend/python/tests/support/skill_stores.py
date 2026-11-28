"""`GraphSkillStore` as one caller holds it: scoped to the caller, reading the whole org, or the builtin seeder's."""

from __future__ import annotations

from typing import Any

from app.agents.agent_loop.skills.builtin_seeder import SEED_IDENTITY
from app.agents.agent_loop.skills.graph_store import GraphSkillStore, SkillVisibility


def skill_store(
    graph: Any,  # noqa: ANN401 - a graph provider or a test's stand-in
    org_id: str, user_id: str, *, visibility_scope: str | None = None, **kwargs: Any,  # noqa: ANN401
) -> GraphSkillStore:
    """`visibility_scope` narrows reads to that person's skills and the builtins; None reads every skill of the org."""
    visibility = SkillVisibility.everyone() if visibility_scope is None else SkillVisibility.of([visibility_scope])
    return GraphSkillStore(
        graph, org_id, user_id, visibility=visibility, allow_builtin_writes=user_id == SEED_IDENTITY, **kwargs,
    )
