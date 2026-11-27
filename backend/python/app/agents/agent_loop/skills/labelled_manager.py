"""`LabelledSkillManager`: a durable run's skill reads, each noting the label of the content it hands the model.

Wraps the run's manager (plain, scoped or pinned) the way `PinnedSkillManager` does. A skill a run's model wrote
carries that run's label (``provenance.py``); every agent-facing read of its content (``load_skill``, the bundles it
stages for code, ``load_skill_resource``, the start hooks' preload) appends ``{"skill", "label"}`` to the request's
``tool_state``, which the citation journal records with the call (``skillLabels``) and the run's own-work label joins
(FOLLOWUPS1-1). A skill a person put in the catalog notes nothing. When the run's journal cannot record the label (an
older Node), such a skill is not handed over at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agents.agent_loop.skills.provenance import content_label, is_source_free

if TYPE_CHECKING:
    from collections.abc import MutableMapping

    from app.agent_loop_lib.modules.providers.skills.base import Skill

__all__ = ["SKILL_LABELS_STATE", "LabelledSkillManager"]

# The request's ``tool_state`` list of the labels of skill content handed to the model; absent: the run's journal
# cannot record them.
SKILL_LABELS_STATE = "skill_labels"


class LabelledSkillManager:
    def __init__(self, manager: Any, state: MutableMapping[str, Any]) -> None:  # noqa: ANN401 - a SkillManager or its wrapper
        self._manager = manager
        self._state = state

    def __getattr__(self, item: str) -> Any:  # noqa: ANN401 - transparent delegation
        return getattr(self._manager, item)

    def _note(self, skill: Skill) -> None:
        label = content_label(skill.metadata)
        if is_source_free(label):
            return
        noted = self._state.get(SKILL_LABELS_STATE)
        if not isinstance(noted, list):
            raise RegistryError(
                f"Skill {skill.name!r} was written by an agent from a conversation and can't be used until "
                "this deployment's server is upgraded"
            )
        entry = {"skill": skill.name, "label": label}
        # One load reads a skill more than once (its body, then the bundle it stages): the label once is enough.
        if entry not in noted:
            noted.append(entry)

    async def activate_skill(self, name: str, session_id: str | None = None) -> Skill:
        skill = await self._manager.activate_skill(name, session_id)
        self._note(skill)
        return skill

    async def load_resource(self, name: str, path: str) -> str:
        # The resource is read from the same content the skill's own read returns, pinned or current.
        self._note(await self._manager.activate_skill(name))
        return await self._manager.load_resource(name, path)

    async def get_resources(self, name: str) -> dict[str, str]:
        self._note(await self._manager.activate_skill(name))
        return await self._manager.get_resources(name)
