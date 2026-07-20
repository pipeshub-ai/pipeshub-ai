from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

"""Skill data model — implements the agentskills.io / anthropic/skills
SKILL.md standard directly (see modules/providers/skills/loader.py for the parser) rather than
inventing our own format, so the existing skill ecosystem
(github.com/anthropics/skills, github.com/openai/skills, ...) works day one.

Progressive disclosure has three levels, matching the spec:
  1. metadata (name + description) — always in context, ~100 words each
  2. SKILL.md body                 — loaded on demand via the load_skill tool
  3. bundled resources (scripts/references/assets) — loaded as needed by the
     agent via the filesystem tools (read_file et al.), never preloaded here

Lifecycle management (category/tags/version/status/source/timestamps) is an
`agent-loop`-namespaced *extension* layered on top of the spec, not a fork of
it: every extended field lives inside the spec's own free-form `metadata`
dict, under the `agent-loop` key (`to_frontmatter_dict`/`from_raw` below are
the round-trip). A SKILL.md authored elsewhere (no such namespace at all)
still loads fine — every extended field has a sensible default — and a
third-party parser that doesn't know about the namespace just sees ordinary,
ignorable metadata.
"""

AGENT_LOOP_NAMESPACE = "agent-loop"


class SkillStatus(str, Enum):
    ACTIVE = "active"
    DRAFT = "draft"
    DEPRECATED = "deprecated"
    CANDIDATE = "candidate"  # pending approval from the learning loop


class SkillSource(str, Enum):
    MANUAL = "manual"                # human-authored
    AGENT_CREATED = "agent_created"  # from the learning loop
    IMPORTED = "imported"            # from an external source/hub
    BUILTIN = "builtin"              # shipped in-repo, seeded per-org (see BuiltinSkillSeeder)


class SkillMetadata(BaseModel):
    """The YAML frontmatter of a SKILL.md file. Spec fields mirror
    agentskills.io/specification#frontmatter exactly; `allowed_tools` is the
    parsed form of the spec's space-separated `allowed-tools` string. Fields
    below that are stored under `metadata["agent-loop"]` on disk.
    """

    # --- agentskills.io spec fields ---
    name: str
    description: str
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    allowed_tools: list[str] | None = None

    # --- agent-loop lifecycle extension (namespaced under metadata["agent-loop"]) ---
    version: str = "1.0.0"
    category: str | None = None       # top-level grouping, e.g. "devops"
    subcategory: str | None = None    # nested grouping, e.g. "kubernetes"
    tags: list[str] = Field(default_factory=list)
    status: SkillStatus = SkillStatus.ACTIVE
    source: SkillSource = SkillSource.MANUAL
    created_at: str | None = None
    updated_at: str | None = None
    deprecated_reason: str | None = None
    replaced_by: str | None = None    # skill name that replaces this one

    # --- connectedness (hierarchical/graph) extension, same namespace ---
    related: list[str] = Field(default_factory=list)   # see-also skill names
    requires: list[str] = Field(default_factory=list)  # skills this one builds on
    concepts: list[str] = Field(default_factory=list)  # free-form concept/topic anchors

    # --- pack provenance (reserved for the future npm-style installer) ---
    pack_name: str | None = None
    pack_version: str | None = None

    @classmethod
    def from_raw(cls, data: dict[str, Any]) -> "SkillMetadata":
        """Build from a parsed YAML frontmatter dict (see loader.py). Pulls
        extended fields out of `metadata["agent-loop"]` when present,
        applying defaults for anything missing — including SKILL.md files
        with no `agent-loop` namespace at all."""
        raw_metadata = dict(data.get("metadata") or {})
        ext = dict(raw_metadata.get(AGENT_LOOP_NAMESPACE) or {})
        allowed_tools_raw = data.get("allowed-tools")
        allowed_tools = allowed_tools_raw.split() if isinstance(allowed_tools_raw, str) else None

        return cls(
            name=data.get("name"),
            description=data.get("description"),
            license=data.get("license"),
            compatibility=data.get("compatibility"),
            metadata=raw_metadata,
            allowed_tools=allowed_tools,
            version=ext.get("version", "1.0.0"),
            category=ext.get("category"),
            subcategory=ext.get("subcategory"),
            tags=list(ext.get("tags") or []),
            status=SkillStatus(ext["status"]) if ext.get("status") else SkillStatus.ACTIVE,
            source=SkillSource(ext["source"]) if ext.get("source") else SkillSource.MANUAL,
            created_at=ext.get("created_at"),
            updated_at=ext.get("updated_at"),
            deprecated_reason=ext.get("deprecated_reason"),
            replaced_by=ext.get("replaced_by"),
            related=list(ext.get("related") or []),
            requires=list(ext.get("requires") or []),
            concepts=list(ext.get("concepts") or []),
            pack_name=ext.get("pack_name"),
            pack_version=ext.get("pack_version"),
        )

    def to_frontmatter_dict(self) -> dict[str, Any]:
        """Serialize back to a YAML-frontmatter-ready dict — the inverse of
        `from_raw`. Extended fields are nested under `metadata["agent-loop"]`
        so the written SKILL.md stays fully spec-compliant; any *other*
        top-level `metadata` keys a human/third-party tool added are
        preserved untouched."""
        data: dict[str, Any] = {"name": self.name, "description": self.description}
        if self.license:
            data["license"] = self.license
        if self.compatibility:
            data["compatibility"] = self.compatibility
        if self.allowed_tools:
            data["allowed-tools"] = " ".join(self.allowed_tools)

        ext: dict[str, Any] = {
            "version": self.version,
            "status": self.status.value,
            "source": self.source.value,
        }
        if self.category:
            ext["category"] = self.category
        if self.subcategory:
            ext["subcategory"] = self.subcategory
        if self.tags:
            ext["tags"] = list(self.tags)
        if self.created_at:
            ext["created_at"] = self.created_at
        if self.updated_at:
            ext["updated_at"] = self.updated_at
        if self.deprecated_reason:
            ext["deprecated_reason"] = self.deprecated_reason
        if self.replaced_by:
            ext["replaced_by"] = self.replaced_by
        if self.related:
            ext["related"] = list(self.related)
        if self.requires:
            ext["requires"] = list(self.requires)
        if self.concepts:
            ext["concepts"] = list(self.concepts)
        if self.pack_name:
            ext["pack_name"] = self.pack_name
        if self.pack_version:
            ext["pack_version"] = self.pack_version

        merged_metadata = dict(self.metadata)
        merged_metadata[AGENT_LOOP_NAMESPACE] = ext
        data["metadata"] = merged_metadata
        return data


class Skill(BaseModel):
    """One loaded skill: frontmatter metadata + Markdown instructions body.

    `root_dir`, when set, is the directory SKILL.md was loaded from — the
    base path for resolving the skill's bundled `scripts/`, `references/`,
    and `assets/` subdirectories. `resources` is a cheap, precomputed
    listing of those subdirectories' relative file paths (level-3
    progressive disclosure — the agent decides which, if any, to load via
    `load_skill_resource`; this module never reads their contents eagerly).
    """

    metadata: SkillMetadata
    body: str
    root_dir: str | None = None
    resources: dict[str, list[str]] = Field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def description(self) -> str:
        return self.metadata.description


class SkillExperience(BaseModel):
    """Per-skill accumulated experience (MUSE-Autoskill pattern) — what the
    `SkillUsageTracker` accumulates from `record_activation`/`record_outcome`
    calls and what `SkillEvaluator.evaluate_existing` reads to flag
    underperforming or unused skills for refinement/deprecation."""

    skill_name: str
    total_activations: int = 0
    successful_outcomes: int = 0
    failed_outcomes: int = 0
    last_activated: str | None = None
    failure_modes: list[str] = Field(default_factory=list)
    improvement_notes: list[str] = Field(default_factory=list)

    @property
    def success_rate(self) -> float:
        total = self.successful_outcomes + self.failed_outcomes
        return self.successful_outcomes / total if total else 1.0


class SkillCandidate(BaseModel):
    """Output of the learning loop, pending governance approval (or
    already auto-approved) — never itself a `Skill` on disk until
    `SkillManager` persists it via the store."""

    candidate_id: str
    name: str
    description: str
    body: str
    category: str | None = None
    subcategory: str | None = None
    tags: list[str] = Field(default_factory=list)
    source_session_id: str | None = None
    source_trajectory_summary: str = ""
    confidence: float = 1.0
    created_at: str
    status: Literal["pending", "approved", "rejected"] = "pending"


class SkillFilter(BaseModel):
    """Search/filter criteria shared by `SkillReader.list_skills` and
    `SkillIndex.search`."""

    query: str | None = None
    category: str | None = None
    subcategory: str | None = None
    tags: list[str] | None = None
    status: SkillStatus | None = None
    source: SkillSource | None = None
    concepts: list[str] | None = None   # match if the skill has at least one of these
    related_to: str | None = None       # match if this skill name appears in `related` or `requires`


class SkillMatch(BaseModel):
    """Search result with relevance score."""

    skill: SkillMetadata
    relevance: float = 1.0
    match_reason: str = ""


def matches_filter(metadata: SkillMetadata, filt: SkillFilter) -> bool:
    """Structural (non-text) filter matching shared by every `SkillReader`/
    `SkillIndex` implementation (filesystem, graph-backed, ...) — extracted
    here so the category/tag/concept/connectedness semantics can't drift
    between backends. Text/relevance scoring (the `query` field) is each
    backend's own concern (substring match for a plain store, token overlap
    or embeddings for an index) and is deliberately NOT covered here."""
    if filt.category is not None and metadata.category != filt.category:
        return False
    if filt.subcategory is not None and metadata.subcategory != filt.subcategory:
        return False
    if filt.status is not None and metadata.status != filt.status:
        return False
    if filt.source is not None and metadata.source != filt.source:
        return False
    if filt.tags and not (set(filt.tags) & set(metadata.tags)):
        return False
    if filt.concepts and not (set(filt.concepts) & set(metadata.concepts)):
        return False
    if filt.related_to is not None and filt.related_to not in (*metadata.related, *metadata.requires):
        return False
    return True


class SkillVersionInfo(BaseModel):
    """One entry in a skill's revision history — returned by
    `SkillHistoryReader.list_versions` (store.py). Deliberately metadata-only
    (no body/resources) so listing history stays cheap; fetch the full
    snapshot via `get_version` only for the one revision actually needed."""

    version: str
    updated_by: str | None = None
    created_at: str
    summary: str = ""
