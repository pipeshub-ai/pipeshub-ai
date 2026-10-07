"""VFS-ready seam between "what a skill's bundled files are" and "how they
become visible to code the agent runs" — introduced so today's coding-
sandbox staging (`StagingSkillMaterializer`, over `input_staging.py`'s
ContextVar) and tomorrow's virtual-filesystem mount are two implementations
of the SAME contract (`SkillMaterializer`), and `LoadSkillTool`/`run_code`
do not change again when the VFS lands — only `register_skill_tools` in
`skills_wiring.py` (the one place that picks the concrete materializer)
does.

`SKILLS_MOUNT_ROOT`/`skill_mount_path` is the single source of truth for
the "a skill's files live under `skills/<name>/` in the sandbox" convention
— every builtin pack's SKILL.md body is hand-written assuming this prefix
(e.g. `python skills/office-utils/scripts/unpack.py`), and `LoadSkillTool`
surfaces it back to the model as `sandbox_root` so an IMPORTED skill (whose
SKILL.md was authored elsewhere, against the bare agentskills.io convention
of paths relative to the skill's own directory) can be told where its files
actually landed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.loader import render_skill_md

if TYPE_CHECKING:
    from app.agent_loop_lib.modules.providers.skills.manager import SkillManager

__all__ = [
    "SKILLS_MOUNT_ROOT",
    "skill_mount_path",
    "SkillBundle",
    "SkillBundleResolver",
    "SkillMaterializer",
    "StagingSkillMaterializer",
]

SKILLS_MOUNT_ROOT = "skills"


def skill_mount_path(name: str) -> str:
    """Where a skill's bundled files live inside a coding sandbox, relative
    to its working directory — the one place this convention is spelled
    out, so the resolver, `LoadSkillTool`'s `sandbox_root` output, and the
    prompt's one-line note about it can never drift apart."""
    return f"{SKILLS_MOUNT_ROOT}/{name}"


class SkillBundle(BaseModel):
    """A resolved, self-contained skill directory ready to be made visible
    to executing code: `SKILL.md` itself (so a skill's own instructions,
    or a script reading a sibling file relative to it, are available too —
    not just its `scripts`/`references`/`assets`) plus every bundled
    resource, all keyed relative to the skill's OWN root (never already
    prefixed with `mount_path`) — `as_sandbox_files()` is the one place
    that prefix is applied."""

    name: str
    mount_path: str
    files: dict[str, bytes]

    def as_sandbox_files(self) -> dict[str, bytes]:
        """`{relative_path: bytes}` -> `{"<mount_path>/<relative_path>": bytes}`
        — the shape `SkillMaterializer` implementations consume."""
        return {f"{self.mount_path}/{rel}": content for rel, content in self.files.items()}


class SkillBundleResolver:
    """Resolves a skill NAME into every `SkillBundle` it needs to run: the
    skill itself, plus every skill it transitively `requires` (cycle-safe —
    a `requires` cycle stops at the first repeat rather than looping
    forever). Depends on `SkillManager` (its `activate_skill` for the full
    body + `get_resources` for a bulk, single-read resource fetch — see
    `SkillReader.get_resources`) rather than a bare `SkillStore`, since it's
    the same manager-side dependency `LoadSkillTool` already has; nothing
    here duplicates governance/validation the manager already owns."""

    def __init__(self, manager: "SkillManager") -> None:
        self._manager = manager

    async def resolve(self, name: str) -> list[SkillBundle]:
        bundles: list[SkillBundle] = []
        await self._resolve_one(name, bundles, seen=set())
        return bundles

    async def _resolve_one(self, name: str, bundles: list[SkillBundle], seen: set[str]) -> None:
        if name in seen:
            return
        seen.add(name)
        try:
            skill = await self._manager.activate_skill(name)
        except RegistryError:
            return

        files: dict[str, bytes] = {"SKILL.md": render_skill_md(skill).encode("utf-8")}
        resources = await self._manager.get_resources(name)
        for path, content in resources.items():
            files[path] = content.encode("utf-8")
        bundles.append(SkillBundle(name=name, mount_path=skill_mount_path(name), files=files))

        for required_name in skill.metadata.requires:
            await self._resolve_one(required_name, bundles, seen)


@runtime_checkable
class SkillMaterializer(Protocol):
    """How a resolved set of `SkillBundle`s becomes visible to code the
    agent runs. `StagingSkillMaterializer` (today) copies bytes into the
    coding sandbox's staging area; a future `VfsSkillMaterializer` mounts
    each bundle read-only at its `mount_path` instead — same call site,
    same paths the model already saw via `sandbox_root`, so swapping the
    implementation in `skills_wiring.register_skill_tools` is the only
    change the VFS migration needs here."""

    async def materialize(self, bundles: list[SkillBundle]) -> None:
        ...


class StagingSkillMaterializer:
    """Default `SkillMaterializer`: adapts `SkillBundle`s onto
    `input_staging.add_staged_skill_resources` — a pure adapter, no
    behavior change from what `LoadSkillTool._stage_resources` did before
    this module existed. Import is local to `materialize()` (not
    module-level) so importing `bundle.py` alone never pulls in the
    sandbox-tooling package for callers that only need the resolver/model
    (e.g. a future non-sandbox `SkillMaterializer`)."""

    async def materialize(self, bundles: list[SkillBundle]) -> None:
        from app.agent_loop_lib.tools.builtin.sandbox.input_staging import (
            add_staged_skill_resources,
        )

        for bundle in bundles:
            add_staged_skill_resources(bundle.as_sandbox_files())
