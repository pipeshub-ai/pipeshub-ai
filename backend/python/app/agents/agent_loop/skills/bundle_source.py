"""`GraphSkillSource`: the harness's `SkillContentSource` port over `GraphSkillStore`.

Lives beside the store rather than in `app.harness` because the harness
never imports the legacy agent packages; the harness receives it injected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from app.agents.agent_loop.skills.graph_store import GraphSkillStore
from harness_core.skills import (
    SkillBundleInvalid,
    SkillBundleUnavailable,
    SkillPin,
    pinned_content,
    skill_bundle_digest,
    validate_skill_files,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from harness_core.skills import SkillRef

# Reads only; never stamped on a document.
SKILL_READER_IDENTITY = "system:skill-bundles"


class GraphSkillSource:
    """Resources are text in the graph store, so a skill's bytes are its UTF-8 text."""

    def __init__(self, graph: IGraphDBProvider) -> None:
        self._graph = graph

    def _store(self, tenant_id: str) -> GraphSkillStore:
        return GraphSkillStore(self._graph, tenant_id, SKILL_READER_IDENTITY)

    async def files(self, tenant_id: str, skill: SkillRef) -> Mapping[str, bytes] | None:
        texts = await self._store(tenant_id).content_files(skill.name, skill.version)
        if texts is None:
            return None
        return {path: text.encode("utf-8") for path, text in texts.items()}

    async def versions(self, tenant_id: str, name: str) -> Sequence[str]:
        return await self._store(tenant_id).content_versions(name)

    async def content_digest(self, tenant_id: str, skill: SkillRef) -> str | None:
        """The digest admission pins for `skill`; None when it has no valid content to pin."""
        files = await self.files(tenant_id, skill)
        if files is None:
            return None
        try:
            return skill_bundle_digest(validate_skill_files(files))
        except SkillBundleInvalid:
            return None


class SkillWithdrawn(SkillBundleUnavailable):
    """The skill was deleted, disabled or is no longer offered to the run since admission."""

    reason = "skill_withdrawn"


class SkillCheck(Protocol):
    """Whether a run may use a skill's pinned content (ADR-SEC-01): raises ``SkillUntrusted`` when it may not."""

    async def check(self, org_id: str, name: str, digest: str, files: Mapping[str, bytes]) -> None: ...


class FirstPartyOnly:
    """The check without a review store: first-party content passes, an imported skill never does (fail closed)."""

    async def check(self, org_id: str, name: str, digest: str, files: Mapping[str, bytes]) -> None:
        from app.harness.supply_chain.skills import (
            IMPORTED,
            SkillUntrusted,
            skill_source,
        )

        if skill_source(files) == IMPORTED:
            raise SkillUntrusted(f"Skill {name!r} is not approved for use", code="artefact_unapproved")


class PinnedSkills:
    """The one reader of a run's pinned skills, for the loop and every engine (FB-11): the content admission pinned,
    from the skill's history whatever it became since, while the skill is still in force for the run and trusted
    (an imported skill: signed or approved at that content, ADR-SEC-01).

    ``SkillWithdrawn`` once it is not in force; ``SkillUntrusted`` when it is not trusted; ``SkillBundleUnavailable``
    when no stored content matches the pin.
    """

    def __init__(
        self,
        graph: IGraphDBProvider,
        org_id: str,
        agent_key: str | None,
        *,
        viewers: Iterable[str | None] = (),
        trust: SkillCheck | None = None,
    ) -> None:
        self._graph = graph
        self._org_id = org_id
        self._agent_key = agent_key
        # The run's person, by each id they are stamped under: their own skills are offered to it (FIX-SKILLS).
        self._viewers = tuple(viewers)
        self._trust = trust or FirstPartyOnly()

    async def files(self, name: str, digest: str) -> Mapping[str, bytes]:
        store = GraphSkillStore(self._graph, self._org_id, SKILL_READER_IDENTITY)
        if not await store.in_force(name, self._agent_key, self._viewers):
            raise SkillWithdrawn(f"Skill {name!r} is no longer available to this run")
        files = await pinned_content(GraphSkillSource(self._graph), self._org_id, SkillPin(name, digest))
        await self._trust.check(self._org_id, name, digest, files)
        return files


__all__ = ["SKILL_READER_IDENTITY", "FirstPartyOnly", "GraphSkillSource", "PinnedSkills", "SkillCheck", "SkillWithdrawn"]
