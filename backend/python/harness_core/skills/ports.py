"""Skill ports (ARCHITECTURE §4.7): where skill content comes from, bundles, and their way into a sandbox."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from harness_core.sandbox.ports import SandboxHandle
    from harness_core.skills.types import SkillBundleRef, SkillPin, SkillRef
    from harness_core.workspace.types import ObjectScope


class SkillContentSource(Protocol):
    """A tenant's skills as bytes: the current content and every archived version.

    Pins stay resolvable only if every content a skill ever had is still
    returned for some version, so a source must keep old content when a
    skill changes or is deleted.
    """

    async def files(self, tenant_id: str, skill: SkillRef) -> Mapping[str, bytes] | None:
        """``SKILL.md`` and resources by relative path; ``None`` for an unknown skill or version."""
        ...

    async def versions(self, tenant_id: str, name: str) -> Sequence[str]:
        """Every version with retrievable content, the current one included; empty if unknown."""
        ...


class SkillBundleBuilder(Protocol):
    async def build(self, scope: ObjectScope, skill: SkillRef) -> SkillBundleRef:
        """Store the skill's content as a bundle addressed by its digest; idempotent.

        ``SkillBundleUnavailable`` for an unknown skill or version,
        ``SkillBundleInvalid`` for content that is not a valid bundle.
        """
        ...

    async def resolve(self, scope: ObjectScope, pin: SkillPin) -> SkillBundleRef:
        """The bundle a run pinned, whatever the skill became since.

        A content digest resolves to the bundle with that digest; a legacy
        ``digest(name, version)`` pin to that version's content.
        ``SkillBundleUnavailable`` when no content matches the pin.
        """
        ...


class SkillMaterializer(Protocol):
    async def materialize(
        self,
        scope: ObjectScope,
        sandbox: SandboxHandle,
        bundles: Sequence[SkillBundleRef],
        *,
        skills_dir: str,
    ) -> None:
        """Each bundle at ``skills_dir/<name>/``, read-only to the sandbox's processes.

        The sandbox must have a read-only scratch mount at ``skills_dir``
        (``skills_mount``). Bytes are checked against each bundle's digest
        before anything is written.
        """
        ...


__all__ = ["SkillBundleBuilder", "SkillContentSource", "SkillMaterializer"]
