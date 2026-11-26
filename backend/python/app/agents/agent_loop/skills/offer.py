"""The skills a run is offered (FIX-SKILLS, ADR-SKL-01): one rule for the chat loop and every engine.

A run is offered the org's built-in packs and the skills its agent assigns. A run whose agent assigns none (the
system assistant, or an agent without an assignment) is offered the person's own skills in their place. A disabled,
deprecated or model-hidden skill is not offered (``is_advertised``, the loop catalog's own predicate), and nothing is
when skills are off for the deployment or the org.

Admission pins the offer by content digest in the run spec (``x.pipeshub.skills``), so every attempt, resume and
replay of the run mounts the same bundles, and the loop reads them through the same pins.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.modules.providers.skills.base import SkillSource, is_advertised
from app.agents.agent_loop.skills.graph_store import GraphSkillStore, SkillVisibility

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.agent_loop_lib.modules.providers.skills.base import SkillMetadata
    from app.agents.agent_loop.context import AgentContext
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["OFFER_READER_IDENTITY", "offered", "offered_skills", "skill_viewers"]

# Reads only; never stamped on a document.
OFFER_READER_IDENTITY = "system:skill-offer"


def skill_viewers(context: AgentContext) -> tuple[str | None, ...]:
    """The run's person by each id their skills are stamped under: the runtime's user id, the graph user key."""
    return (context.user_id, context.user_info.get("_key"))


def offered(metadata: SkillMetadata, assigned: frozenset[str]) -> bool:
    """Whether a skill the reader sees is offered to a run whose agent assigns ``assigned`` (empty: none)."""
    if not is_advertised(metadata):
        return False
    if metadata.source == SkillSource.BUILTIN:
        return True
    # An assignment replaces the person's own skills: an agent runs with what its editor gave it.
    return metadata.name in assigned if assigned else True


async def offered_skills(
    graph: IGraphDBProvider, org_id: str, viewers: Iterable[str | None], assigned: Iterable[str] = (),
) -> tuple[SkillMetadata, ...]:
    """The skills offered to a run of ``viewers`` (the person, by each id they are stamped under), by name.

    Builtins are seeded first when this process has not reconciled the org yet, so a fresh org's first run is
    offered them; the reconcile never raises."""
    from app.agents.agent_loop.skills.manager_factory import reconcile_builtin_skills

    await reconcile_builtin_skills(graph, org_id)
    names = frozenset(assigned)
    store = GraphSkillStore(
        graph, org_id, OFFER_READER_IDENTITY, visibility=SkillVisibility.of(viewers, shared=names),
    )
    return tuple(sorted((m for m in await store.list_skills() if offered(m, names)), key=lambda m: m.name))
