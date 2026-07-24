from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.base import (
    SkillMetadata,
    SkillSource,
    SkillStatus,
)
from app.agent_loop_lib.modules.providers.skills.validator import SkillFormatError
from app.agent_loop_lib.tools.base import (
    ParameterType,
    Tag,
    Tool,
    ToolOutput,
    ToolParameter,
)
from app.agent_loop_lib.tools.builtin.sandbox.input_staging import add_staged_skill_resources

if TYPE_CHECKING:
    from app.agent_loop_lib.modules.providers.skills.manager import SkillManager
    from app.agent_loop_lib.modules.providers.skills.base import Skill

"""Agent-facing skill tools — thin adapters over `SkillManager`, the single
authority for every skill operation (SOLID — Dependency Inversion: tools
depend on the manager, never on a concrete `SkillStore`/`SkillIndex`
directly). Progressive disclosure across the toolset:

  skills_list / skill_search  -> tier 1 (name+description, filtered/ranked)
  load_skill                  -> tier 2 (full body, on demand)
  load_skill_resource         -> tier 3 (a bundled scripts/references/assets file)
  skill_manage                -> the write surface (create/edit/patch/delete/
                                  deprecate/write_file/remove_file), action-
                                  dispatched like Hermes's `skill_manage` tool.
"""


def _overview(metadata: SkillMetadata) -> dict[str, Any]:
    return {
        "name": metadata.name,
        "description": metadata.description,
        "category": metadata.category,
        "subcategory": metadata.subcategory,
        "tags": metadata.tags,
        "status": metadata.status.value,
        "source": metadata.source.value,
    }


def _render_skill_md(
    name: str,
    description: str,
    body: str,
    category: str | None,
    subcategory: str | None,
    tags: list[str] | None,
) -> str:
    """Build a full SKILL.md string for a skill authored through
    `skill_manage` — source is always tagged AGENT_CREATED, since anything
    written through this tool was authored by an agent (whether from the
    learning loop or an interactive session), distinct from `MANUAL`
    (reserved for SKILL.md files a human placed on disk directly)."""
    metadata = SkillMetadata(
        name=name, description=description, category=category, subcategory=subcategory,
        tags=list(tags or []), source=SkillSource.AGENT_CREATED,
    )
    frontmatter_yaml = yaml.safe_dump(metadata.to_frontmatter_dict(), sort_keys=False)
    return f"---\n{frontmatter_yaml}---\n\n{body.strip()}\n"


class SkillsListTool(Tool):
    """`skills_list` — browse the catalog with optional category/tag/status
    filters. Complements the always-in-prompt overview (see agent/prompt.py)
    for a library too large to render in full every turn."""

    def __init__(self, manager: "SkillManager") -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "skills_list"

    @property
    def short_description(self) -> str:
        return "List available skills, optionally filtered by category/tag/status."

    @property
    def description(self) -> str:
        return (
            "List available skills with optional filters. Deprecated skills are "
            "excluded unless you explicitly filter for status='deprecated'."
        )

    @property
    def path(self) -> str:
        return "/toolsets/skills/skills_list"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="category", type=ParameterType.STRING, required=False, description="Filter by top-level category."),
            ToolParameter(name="subcategory", type=ParameterType.STRING, required=False, description="Filter by subcategory."),
            ToolParameter(
                name="tags", type=ParameterType.ARRAY, required=False,
                description="Filter to skills having at least one of these tags.",
                items={"type": "string"},
            ),
            ToolParameter(
                name="status", type=ParameterType.STRING, required=False,
                description="Filter by lifecycle status.",
                enum=[s.value for s in SkillStatus],
            ),
        ]

    async def execute(
        self,
        category: str | None = None,
        subcategory: str | None = None,
        tags: list[str] | None = None,
        status: str | None = None,
        **kwargs: Any,
    ) -> ToolOutput:
        from app.agent_loop_lib.modules.providers.skills.base import SkillFilter

        status_enum = SkillStatus(status) if status else None
        filt = SkillFilter(category=category, subcategory=subcategory, tags=tags, status=status_enum)
        metadatas = await self._manager.list_skills(filt)
        if status_enum is None:
            metadatas = [m for m in metadatas if m.status != SkillStatus.DEPRECATED]
        return ToolOutput(success=True, data={"skills": [_overview(m) for m in metadatas], "count": len(metadatas)})


class SkillSearchTool(Tool):
    """`skill_search` — keyword search across name/description/tags/category,
    for a library too large to browse in full via `skills_list`."""

    def __init__(self, manager: "SkillManager") -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "skill_search"

    @property
    def short_description(self) -> str:
        return "Keyword-search skills by name, description, tags, or category."

    @property
    def description(self) -> str:
        return (
            "Search skills by keyword across name, description, tags, and category. "
            "Use this when the catalog is too large to eyeball, or you're not sure "
            "of a skill's exact name."
        )

    @property
    def path(self) -> str:
        return "/toolsets/skills/skill_search"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="query", type=ParameterType.STRING, required=True, description="Search keywords."),
            ToolParameter(name="category", type=ParameterType.STRING, required=False, description="Restrict to this category."),
            ToolParameter(
                name="limit", type=ParameterType.INTEGER, required=False, default=10,
                description="Max number of results (default 10).",
            ),
        ]

    async def execute(self, query: str, category: str | None = None, limit: int = 10, **kwargs: Any) -> ToolOutput:
        matches = await self._manager.search(query, category=category, limit=limit)
        return ToolOutput(success=True, data={
            "matches": [
                {**_overview(m.skill), "relevance": m.relevance, "match_reason": m.match_reason}
                for m in matches
            ],
        })


class LoadSkillTool(Tool):
    """`load_skill` — the level-2 progressive disclosure step of the
    agentskills.io spec: the model sees every skill's name+description
    upfront at near-zero token cost, then calls this tool to fetch the full
    Markdown instructions body only for the one skill it actually needs."""

    def __init__(self, manager: "SkillManager") -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "load_skill"

    @property
    def short_description(self) -> str:
        return "Load the full instructions for a skill by exact name."

    @property
    def description(self) -> str:
        return (
            "Load the full instructions for a skill by exact name (see the "
            "skills list in your system prompt, or skills_list/skill_search). "
            "Call this ONLY immediately before you execute the step the skill "
            "covers — NOT at the beginning of a multi-step task. Complete all "
            "prerequisite work first, then load the skill right before you need it."
        )

    @property
    def path(self) -> str:
        return "/toolsets/skills/load_skill"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="name", type=ParameterType.STRING, required=True, description="Exact skill name from the overview."),
        ]

    async def execute(self, name: str, **kwargs: Any) -> ToolOutput:
        try:
            skill = await self._manager.activate_skill(name)
        except RegistryError:
            return ToolOutput(success=True, data={"error": f"Unknown skill: {name!r}."})

        await self._stage_resources(skill)

        data: dict[str, Any] = {
            "name": skill.name,
            "description": skill.description,
            "body": skill.body,
            "license": skill.metadata.license,
            "compatibility": skill.metadata.compatibility,
            "allowed_tools": skill.metadata.allowed_tools,
            "root_dir": skill.root_dir,
            "category": skill.metadata.category,
            "subcategory": skill.metadata.subcategory,
            "tags": skill.metadata.tags,
            "resources": skill.resources,
        }
        if skill.metadata.status == SkillStatus.DEPRECATED:
            data["deprecated"] = True
            data["deprecated_reason"] = skill.metadata.deprecated_reason
            data["replaced_by"] = skill.metadata.replaced_by
        return ToolOutput(success=True, data=data)

    async def _stage_resources(self, skill: "Skill", *, _seen: set[str] | None = None) -> None:
        """Copies `skill`'s bundled `scripts/`/`references/`/`assets/`
        files (and, transitively, every skill it `requires`) into the
        staging area so the NEXT freshly-created coding sandbox gets them
        at `skills/<name>/<path>` — matching exactly the paths a skill's
        own SKILL.md body points to (e.g. `python skills/office-utils/
        scripts/unpack.py`). `_seen` guards against a `requires` cycle;
        best-effort throughout — a resource/skill lookup failure is
        skipped rather than failing the whole `load_skill` call over an
        optional convenience."""
        seen = _seen if _seen is not None else set()
        if skill.name in seen:
            return
        seen.add(skill.name)

        paths = [path for kind_paths in skill.resources.values() for path in kind_paths]
        if paths:
            files: dict[str, bytes] = {}
            for path in paths:
                try:
                    content = await self._manager.load_resource(skill.name, path)
                except RegistryError:
                    continue
                files[f"skills/{skill.name}/{path}"] = content.encode("utf-8")
            add_staged_skill_resources(files)

        for required_name in skill.metadata.requires:
            try:
                required_skill = await self._manager.activate_skill(required_name)
            except RegistryError:
                continue
            await self._stage_resources(required_skill, _seen=seen)


class LoadSkillResourceTool(Tool):
    """`load_skill_resource` — level-3 progressive disclosure: a specific
    bundled file (script/reference/asset) a loaded skill's body pointed to."""

    def __init__(self, manager: "SkillManager") -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "load_skill_resource"

    @property
    def short_description(self) -> str:
        return "Load a bundled resource file (script/reference/asset) for a skill."

    @property
    def description(self) -> str:
        return (
            "Load the contents of one of a skill's bundled resource files (see "
            "the 'resources' field returned by load_skill) — a script, reference "
            "document, or asset the skill's instructions point to."
        )

    @property
    def path(self) -> str:
        return "/toolsets/skills/load_skill_resource"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="name", type=ParameterType.STRING, required=True, description="The skill's exact name."),
            ToolParameter(
                name="path", type=ParameterType.STRING, required=True,
                description="Resource path relative to the skill root, e.g. 'scripts/deploy.sh'.",
            ),
        ]

    async def execute(self, name: str, path: str, **kwargs: Any) -> ToolOutput:
        try:
            content = await self._manager.load_resource(name, path)
        except RegistryError as e:
            return ToolOutput(success=True, data={"error": str(e)})
        return ToolOutput(success=True, data={"name": name, "path": path, "content": content})


class SkillManageTool(Tool):
    """`skill_manage` — the write surface for skills (Hermes's action-dispatch
    pattern): create, edit, patch, delete, deprecate, write_file, remove_file,
    plus three read-only governance/lifecycle actions — versions, rollback,
    health — that expose `SkillManager.list_versions`/`rollback`/
    `evaluate_skill_health` (previously manager-only methods with no tool
    surface) so an agent (or the skill_writer role) can inspect a skill's
    revision history, restore a prior version, or get a keep/refine/
    deprecate recommendation without a human going around the tool layer.
    Persists through `SkillManager`, which validates and re-indexes on every
    mutation. Anything WRITTEN through this tool is tagged
    `source=agent_created` — see `_render_skill_md`."""

    def __init__(self, manager: "SkillManager") -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "skill_manage"

    @property
    def short_description(self) -> str:
        return "Create, edit, patch, delete, or deprecate a skill (or its bundled files)."

    @property
    def description(self) -> str:
        return (
            "Manage the skill library. Actions:\n"
            "- create: requires name, description, body (kebab-case name, one-line "
            "description of WHEN to use it, full Markdown instructions). Optional "
            "category, subcategory, tags.\n"
            "- edit: requires name; description/body/category/subcategory/tags are "
            "optional partial updates (unset fields keep their current value).\n"
            "- patch: requires name, old_string, new_string — exact-match replace "
            "against the skill's body (old_string must match exactly once).\n"
            "- delete: requires name.\n"
            "- deprecate: requires name, reason; replaced_by is optional.\n"
            "- write_file: requires name, path, file_content — writes a bundled "
            "resource file (path relative to the skill root, e.g. 'scripts/run.sh').\n"
            "- remove_file: requires name, path.\n"
            "- versions: requires name — lists the skill's revision history "
            "(newest first), if the configured store supports it.\n"
            "- rollback: requires name, and version (the version string from "
            "'versions') — restores the skill to that prior revision.\n"
            "- health: requires name — returns a keep/refine/deprecate "
            "recommendation based on recorded usage outcomes."
        )

    @property
    def path(self) -> str:
        return "/toolsets/skills/skill_manage"

    @property
    def tags(self) -> list[Tag]:
        return [Tag("category", "write")]

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="action", type=ParameterType.STRING, required=True,
                description=(
                    "One of: create, edit, patch, delete, deprecate, write_file, "
                    "remove_file, versions, rollback, health."
                ),
                enum=[
                    "create", "edit", "patch", "delete", "deprecate", "write_file",
                    "remove_file", "versions", "rollback", "health",
                ],
            ),
            ToolParameter(name="name", type=ParameterType.STRING, required=True, description="Lowercase kebab-case skill name."),
            ToolParameter(name="description", type=ParameterType.STRING, required=False, description="One-line 'when to use' description."),
            ToolParameter(name="body", type=ParameterType.STRING, required=False, description="Full Markdown instructions body."),
            ToolParameter(name="category", type=ParameterType.STRING, required=False, description="Top-level category, e.g. 'devops'."),
            ToolParameter(name="subcategory", type=ParameterType.STRING, required=False, description="Nested category."),
            ToolParameter(name="tags", type=ParameterType.ARRAY, required=False, description="Freeform tags.", items={"type": "string"}),
            ToolParameter(name="old_string", type=ParameterType.STRING, required=False, description="[patch] Exact text to replace in the body."),
            ToolParameter(name="new_string", type=ParameterType.STRING, required=False, description="[patch] Replacement text."),
            ToolParameter(name="reason", type=ParameterType.STRING, required=False, description="[deprecate] Why this skill is deprecated."),
            ToolParameter(name="replaced_by", type=ParameterType.STRING, required=False, description="[deprecate] Name of the replacement skill, if any."),
            ToolParameter(name="path", type=ParameterType.STRING, required=False, description="[write_file/remove_file] Path relative to the skill root."),
            ToolParameter(name="file_content", type=ParameterType.STRING, required=False, description="[write_file] Contents to write."),
            ToolParameter(name="version", type=ParameterType.STRING, required=False, description="[rollback] Version string to restore, from 'versions'."),
        ]

    async def execute(
        self,
        action: str,
        name: str,
        description: str | None = None,
        body: str | None = None,
        category: str | None = None,
        subcategory: str | None = None,
        tags: list[str] | None = None,
        old_string: str | None = None,
        new_string: str | None = None,
        reason: str | None = None,
        replaced_by: str | None = None,
        path: str | None = None,
        file_content: str | None = None,
        version: str | None = None,
        **kwargs: Any,
    ) -> ToolOutput:
        try:
            if action == "create":
                if not description or not body:
                    return ToolOutput(success=False, error="'create' requires 'description' and 'body'")
                content = _render_skill_md(name, description, body, category, subcategory, tags)
                metadata = await self._manager.create(name, content, category, subcategory)
                return ToolOutput(success=True, data={"name": name, "created": True, "category": metadata.category})

            if action == "edit":
                existing = await self._manager.activate_skill(name)
                content = _render_skill_md(
                    name,
                    description if description is not None else existing.description,
                    body if body is not None else existing.body,
                    category if category is not None else existing.metadata.category,
                    subcategory if subcategory is not None else existing.metadata.subcategory,
                    tags if tags is not None else existing.metadata.tags,
                )
                await self._manager.update(name, content)
                return ToolOutput(success=True, data={"name": name, "updated": True})

            if action == "patch":
                if old_string is None or new_string is None:
                    return ToolOutput(success=False, error="'patch' requires 'old_string' and 'new_string'")
                ok = await self._manager.patch(name, old_string, new_string)
                return ToolOutput(
                    success=ok, data={"name": name, "patched": ok},
                    error=None if ok else f"'{old_string[:80]}' not found (or not unique) in {name!r}'s body",
                )

            if action == "delete":
                ok = await self._manager.delete(name)
                return ToolOutput(
                    success=ok, data={"name": name, "deleted": ok},
                    error=None if ok else f"Skill {name!r} not found, or lives in a read-only root",
                )

            if action == "deprecate":
                if not reason:
                    return ToolOutput(success=False, error="'deprecate' requires 'reason'")
                ok = await self._manager.deprecate(name, reason, replaced_by)
                return ToolOutput(
                    success=ok, data={"name": name, "deprecated": ok},
                    error=None if ok else f"Skill {name!r} not found, or lives in a read-only root",
                )

            if action == "write_file":
                if path is None or file_content is None:
                    return ToolOutput(success=False, error="'write_file' requires 'path' and 'file_content'")
                ok = await self._manager.write_resource(name, path, file_content)
                return ToolOutput(
                    success=ok, data={"name": name, "path": path, "written": ok},
                    error=None if ok else f"Skill {name!r} not found, or lives in a read-only root",
                )

            if action == "remove_file":
                if path is None:
                    return ToolOutput(success=False, error="'remove_file' requires 'path'")
                ok = await self._manager.remove_resource(name, path)
                return ToolOutput(
                    success=ok, data={"name": name, "path": path, "removed": ok},
                    error=None if ok else f"Resource {path!r} not found for skill {name!r}",
                )

            if action == "versions":
                versions = await self._manager.list_versions(name)
                return ToolOutput(success=True, data={
                    "name": name,
                    "versions": [v.model_dump(mode="json") for v in versions],
                })

            if action == "rollback":
                if not version:
                    return ToolOutput(success=False, error="'rollback' requires 'version'")
                metadata = await self._manager.rollback(name, version)
                return ToolOutput(success=True, data={"name": name, "rolled_back_to": version, "version": metadata.version})

            if action == "health":
                recommended_action, health_reason = await self._manager.evaluate_skill_health(name)
                return ToolOutput(success=True, data={
                    "name": name, "recommended_action": recommended_action, "reason": health_reason,
                })

            return ToolOutput(success=False, error=f"Unknown action: {action!r}")
        except RegistryError as e:
            return ToolOutput(success=False, error=str(e))
        except SkillFormatError as e:
            return ToolOutput(success=False, error=str(e))
