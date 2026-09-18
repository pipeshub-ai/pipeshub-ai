from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.base import (
    SkillInUseError,
    SkillMetadata,
    SkillSource,
    SkillStatus,
    is_advertised,
)
from app.agent_loop_lib.modules.providers.skills.bundle import (
    SkillBundleResolver,
    SkillMaterializer,
    StagingSkillMaterializer,
    skill_mount_path,
)
from app.agent_loop_lib.modules.providers.skills.validator import SkillFormatError
from app.agent_loop_lib.tools.base import (
    ParameterType,
    Tag,
    Tool,
    ToolOutput,
    ToolParameter,
)

if TYPE_CHECKING:
    from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
    from app.agent_loop_lib.modules.providers.skills.manager import SkillManager

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


def _error_summary(result: "CoreToolResult") -> str | None:
    """Shared first step for every skill tool's `summarize_result`: a
    genuine execution failure (`ToolOutput(success=False, ...)`) surfaces
    as a plain error string in `result.content` — never a SKILL.md body or
    file content, so it's always safe to show as-is. Returns `None` when
    the call succeeded, so the caller falls through to its own
    success-path formatting."""
    if result.is_error and isinstance(result.content, str):
        return result.content
    return None


# `skill_manage`'s `summarize_args`/`summarize_result` verb tables, keyed by
# the `action` parameter — present-continuous for "about to do" (args),
# past-tense for "did" (result), mirroring the tense split every other
# skill tool's display_name/summaries already follow.
_MANAGE_PRESENT_VERBS: dict[str, str] = {
    "create": "Creating", "edit": "Editing", "patch": "Patching",
    "delete": "Deleting", "deprecate": "Deprecating",
    "write_file": "Writing a file for", "remove_file": "Removing a file from",
    "versions": "Listing versions of", "rollback": "Rolling back",
    "health": "Checking health of",
}
_MANAGE_PAST_VERBS: dict[str, str] = {
    "create": "Created", "edit": "Updated", "patch": "Patched",
    "delete": "Deleted", "deprecate": "Deprecated",
    "write_file": "Wrote a file for", "remove_file": "Removed a file from",
    "versions": "Listed versions of", "rollback": "Rolled back",
    "health": "Checked health of",
}


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
    *,
    existing: SkillMetadata | None = None,
) -> str:
    """Build a full SKILL.md string for a skill authored through
    `skill_manage` — source is always tagged AGENT_CREATED on a fresh
    `create`, since anything written through this tool was authored by an
    agent (whether from the learning loop or an interactive session),
    distinct from `MANUAL` (reserved for SKILL.md files a human placed on
    disk directly).

    `existing`, when given (the `edit` action only — never `create`, which
    has no prior state), carries the current `source`/`status`/
    `deprecated_reason`/`replaced_by` forward so an `edit` can never
    silently re-activate a disabled/deprecated skill — `GraphSkillStore.
    _skill_to_doc` writes the graph `status`/`source` columns straight from
    whatever metadata this function's caller passes to `update`."""
    metadata = SkillMetadata(
        name=name, description=description, category=category, subcategory=subcategory,
        tags=list(tags or []),
        source=existing.source if existing is not None else SkillSource.AGENT_CREATED,
        status=existing.status if existing is not None else SkillStatus.ACTIVE,
        deprecated_reason=existing.deprecated_reason if existing is not None else None,
        replaced_by=existing.replaced_by if existing is not None else None,
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
    def display_name(self) -> str | None:
        return "Listed skills"

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
            metadatas = [m for m in metadatas if is_advertised(m)]
        return ToolOutput(success=True, data={"skills": [_overview(m) for m in metadatas], "count": len(metadatas)})

    def summarize_args(self, args: dict[str, Any]) -> str | None:
        bits = [
            f"{key}={value}"
            for key, value in (
                ("category", args.get("category")),
                ("subcategory", args.get("subcategory")),
                ("status", args.get("status")),
            )
            if value
        ]
        tags = args.get("tags")
        if tags:
            bits.append(f"tags={', '.join(tags)}")
        return f"Listing skills ({', '.join(bits)})" if bits else "Listing skills"

    def summarize_result(self, args: dict[str, Any], result: "CoreToolResult") -> str | None:
        error = _error_summary(result)
        if error is not None:
            return error
        content = result.content
        count = content.get("count") if isinstance(content, dict) else None
        if count is None:
            return None
        return f"Found {count} skill{'' if count == 1 else 's'}"


class SkillSearchTool(Tool):
    """`skill_search` — keyword search across name/description/tags/category,
    for a library too large to browse in full via `skills_list`."""

    def __init__(self, manager: "SkillManager") -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "skill_search"

    @property
    def display_name(self) -> str | None:
        return "Searched skills"

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

    def summarize_args(self, args: dict[str, Any]) -> str | None:
        query = args.get("query")
        return f'Searching skills: "{query}"' if isinstance(query, str) and query.strip() else "Searching skills"

    def summarize_result(self, args: dict[str, Any], result: "CoreToolResult") -> str | None:
        error = _error_summary(result)
        if error is not None:
            return error
        content = result.content
        matches = content.get("matches") if isinstance(content, dict) else None
        if matches is None:
            return None
        count = len(matches)
        query = args.get("query")
        suffix = f' matching "{query}"' if query else ""
        return f"Found {count} skill{'' if count == 1 else 's'}{suffix}"


class LoadSkillTool(Tool):
    """`load_skill` — the level-2 progressive disclosure step of the
    agentskills.io spec: the model sees every skill's name+description
    upfront at near-zero token cost, then calls this tool to fetch the full
    Markdown instructions body only for the one skill it actually needs."""

    def __init__(
        self,
        manager: "SkillManager",
        *,
        resolver: SkillBundleResolver | None = None,
        materializer: SkillMaterializer | None = None,
    ) -> None:
        self._manager = manager
        self._resolver = resolver or SkillBundleResolver(manager)
        self._materializer = materializer or StagingSkillMaterializer()

    @property
    def name(self) -> str:
        return "load_skill"

    @property
    def display_name(self) -> str | None:
        return "Loaded skill"

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
        except RegistryError as e:
            message = str(e)
            error = message if "is disabled" in message else f"Unknown skill: {name!r}."
            return ToolOutput(success=True, data={"error": error})

        if skill.metadata.status == SkillStatus.DISABLED:
            # Unlike DEPRECATED (still loadable, just not advertised — see
            # below), an owner-disabled skill must not load at all: disable
            # is a reversible "don't offer this to agents right now" mute,
            # not a "loadable but discouraged" archive.
            return ToolOutput(success=True, data={"error": f"Skill {name!r} is disabled."})

        # Resolves + stages `skill` and (transitively) every skill it
        # `requires` so the NEXT freshly-created coding sandbox has them at
        # `skills/<name>/<path>` — matching exactly the paths a skill's own
        # SKILL.md body points to (e.g. `python skills/office-utils/
        # scripts/unpack.py`). Best-effort: `SkillBundleResolver` skips a
        # lookup failure rather than failing this whole call over an
        # optional convenience.
        bundles = await self._resolver.resolve(name)
        await self._materializer.materialize(bundles)

        data: dict[str, Any] = {
            "name": skill.name,
            "description": skill.description,
            "body": skill.body,
            "license": skill.metadata.license,
            "compatibility": skill.metadata.compatibility,
            "allowed_tools": skill.metadata.allowed_tools,
            "sandbox_root": skill_mount_path(name),
            "sandbox_note": (
                "Bundled files are available to run_code under sandbox_root. "
                "Relative paths resolve from the sandbox working directory — "
                "prefix with sandbox_root or cd there first."
            ),
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

    def summarize_args(self, args: dict[str, Any]) -> str | None:
        name = args.get("name")
        return f"Loading skill {name}" if name else "Loading skill"

    def summarize_result(self, args: dict[str, Any], result: "CoreToolResult") -> str | None:
        """Never surfaces `body`/`resources` (the full SKILL.md instructions
        and bundled file listing) — only the name and deprecation status,
        same bound the frontend's `resultPreview` fallback relies on."""
        error = _error_summary(result)
        if error is not None:
            return error
        content = result.content
        if not isinstance(content, dict):
            return None
        unknown = content.get("error")
        if unknown:
            return unknown
        name = content.get("name") or args.get("name", "")
        return f"Loaded skill {name} (deprecated)" if content.get("deprecated") else f"Loaded skill {name}"


class LoadSkillResourceTool(Tool):
    """`load_skill_resource` — level-3 progressive disclosure: a specific
    bundled file (script/reference/asset) a loaded skill's body pointed to."""

    def __init__(self, manager: "SkillManager") -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "load_skill_resource"

    @property
    def display_name(self) -> str | None:
        return "Loaded skill file"

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

    def summarize_args(self, args: dict[str, Any]) -> str | None:
        name, path = args.get("name"), args.get("path")
        return f"Loading {name}/{path}" if name and path else "Loading skill file"

    def summarize_result(self, args: dict[str, Any], result: "CoreToolResult") -> str | None:
        """Never surfaces the loaded file's `content` — only its name/path,
        same bound `LoadSkillTool.summarize_result` applies to a skill's
        body."""
        error = _error_summary(result)
        if error is not None:
            return error
        content = result.content
        if not isinstance(content, dict):
            return None
        unknown = content.get("error")
        if unknown:
            return unknown
        name = content.get("name") or args.get("name", "")
        path = content.get("path") or args.get("path", "")
        return f"Loaded {name}/{path}" if name or path else "Loaded skill file"


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
    def display_name(self) -> str | None:
        return "Managed skill"

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
                existing = await self._manager.get_skill(name)
                content = _render_skill_md(
                    name,
                    description if description is not None else existing.description,
                    body if body is not None else existing.body,
                    category if category is not None else existing.metadata.category,
                    subcategory if subcategory is not None else existing.metadata.subcategory,
                    tags if tags is not None else existing.metadata.tags,
                    existing=existing.metadata,
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
        except SkillInUseError as e:
            return ToolOutput(success=False, error=str(e))
        except RegistryError as e:
            return ToolOutput(success=False, error=str(e))
        except SkillFormatError as e:
            return ToolOutput(success=False, error=str(e))

    def summarize_args(self, args: dict[str, Any]) -> str | None:
        action, name = args.get("action"), args.get("name")
        if not name:
            return None
        verb = _MANAGE_PRESENT_VERBS.get(action, "Managing")
        return f"{verb} skill {name}"

    def summarize_result(self, args: dict[str, Any], result: "CoreToolResult") -> str | None:
        error = _error_summary(result)
        if error is not None:
            return error
        content = result.content
        if not isinstance(content, dict):
            return None
        action = args.get("action")
        name = content.get("name") or args.get("name", "")
        if action == "rollback":
            version = content.get("rolled_back_to")
            return f"Rolled back skill {name} to {version}" if version else f"Rolled back skill {name}"
        if action == "health":
            recommendation = content.get("recommended_action")
            return (
                f"Skill {name} health check: {recommendation}"
                if recommendation else f"Checked health of skill {name}"
            )
        if action == "versions":
            count = len(content.get("versions") or [])
            return f"Listed {count} version{'' if count == 1 else 's'} of skill {name}"
        verb = _MANAGE_PAST_VERBS.get(action, "Updated")
        return f"{verb} skill {name}"
