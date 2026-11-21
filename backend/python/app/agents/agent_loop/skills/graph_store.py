"""`GraphSkillStore`: agent_loop_lib's `SkillStore` + `SkillHistoryReader` +
`SkillCandidateStore` backed by `IGraphDBProvider` — works unmodified
against either `ArangoHTTPProvider` or `Neo4jProvider` since it only calls
the generic, backend-agnostic surface of that interface (`get_document`,
`batch_upsert_nodes`, `update_node`, `update_node_if_match`,
`delete_nodes`/`delete_nodes_and_edges`, `batch_create_edges`,
`delete_edges_from`, `get_nodes_by_filters`) — never
`execute_query` (that method is explicitly database-specific: AQL for
Arango, Cypher for Neo4j, so using it here would silently break whichever
backend isn't the one currently deployed).

Constructed per request with `(graph_provider, org_id, user_id)` — tenant
scoping is bound at construction (same pattern as every other PipesHub
adapter), so none of the `SkillStore`/`SkillHistoryReader`/
`SkillCandidateStore` methods take an org/user parameter themselves. Every
read applies a hard `orgId` equality filter (the tenant boundary); every
write stamps `orgId`/`createdBy` and creates an OWNER `permission` edge
from the acting user to the skill, so fine-grained user/team visibility
can be layered on later (the edges already exist) without a migration.

Document design (see `app/schema/arango/documents.py` for the exact JSON
Schemas): `agentSkills` stores the full rendered SKILL.md in `content`
plus every frontmatter field denormalized for fast, no-parse listing;
`agentSkillVersions` is an append-only log of prior full snapshots, one
document per revision; `agentSkillRelation` edges materialize
`related`/`requires`/`replaced_by` as a real skill-to-skill graph,
re-synced from frontmatter on every create/update.

Because Neo4j node properties only allow primitives or arrays of
primitives (no nested maps), anything map-shaped is stored as
index-aligned parallel string arrays that both backends accept natively:
bundled resources as `resourcePaths`/`resourceContents` (see
`_resources_to_fields`/`_resources_from_doc`) and the `AuditGovernor`
trail as `auditActions`/`auditActorIds`/`auditReasons`/`auditTimestamps`.

`get_nodes_by_filters` does plain equality filtering only, and — critically
— the two backends disagree on what "no `return_fields`" returns in a way
that matters here: ArangoDB hands back the raw document (`_key`/`_id`),
Neo4j hands back the node's own properties (which already include an
`id` property — see `_arango_to_neo4j_node`). This store therefore never
passes `return_fields`, always reads the identifier via
`doc.get("id") or doc.get("_key")`, and applies any filter beyond a flat
`orgId`/`status`/`name` equality check (list-membership filters like
`tags`/`concepts`) in Python after the fetch — the catalog is hundreds of
documents per org, not millions, so this stays cheap.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.commands.render import CommandArgument, parse_arguments
from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.base import (
    Skill,
    SkillCandidate,
    SkillConflictError,
    SkillFilter,
    SkillMetadata,
    SkillProvenance,
    SkillReferentialUsage,
    SkillSource,
    SkillStatus,
    SkillVersionInfo,
    matches_filter,
)
from app.agent_loop_lib.modules.providers.skills.loader import (
    parse_skill_md,
    render_skill_md,
)
from app.agent_loop_lib.modules.providers.skills.store import (
    SkillCandidateStore,
    SkillHistoryReader,
    SkillStore,
)
from app.agent_loop_lib.modules.providers.skills.validator import SkillValidator
from app.agents.agent_loop.skills.provenance import (
    UNKNOWN_LABEL,
    content_label,
    join_wire,
)
from app.config.constants.arangodb import CollectionNames
from app.core.org_context import org_of
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = [
    "GraphSkillStore",
    "SharedSkillRights",
    "SkillLabels",
    "SkillScope",
    "SkillSharing",
    "SkillVisibility",
    "erase_org_skill_candidates",
    "erase_session_learned_skills",
    "erase_session_skill_candidates",
    "erase_skill_author",
]

_SKILLS = CollectionNames.AGENT_SKILLS.value
_VERSIONS = CollectionNames.AGENT_SKILL_VERSIONS.value
_CANDIDATES = CollectionNames.AGENT_SKILL_CANDIDATES.value
_RELATION = CollectionNames.AGENT_SKILL_RELATION.value
_PERMISSION = CollectionNames.PERMISSION.value
_USERS = CollectionNames.USERS.value
_AGENT_HAS_SKILL = CollectionNames.AGENT_HAS_SKILL.value
_AGENT_INSTANCES = CollectionNames.AGENT_INSTANCES.value

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _bump_patch(version: str) -> str:
    """Storage-level semver bump (the plan's "versioning is a storage
    concern") — a non-semver or missing version resets to '1.0.1' rather
    than raising, since a version string is never user-validated input."""
    match = _SEMVER_RE.match(version or "")
    if not match:
        return "1.0.1"
    major, minor, patch = (int(x) for x in match.groups())
    return f"{major}.{minor}.{patch + 1}"


def _next_updated_at(existing: Any, now: int | None = None) -> int:
    """If-Match tokens are millisecond timestamps. Two writes in the same
    millisecond must still advance the token, or a later CAS still matches
    the prior value."""
    clock = get_epoch_timestamp_in_ms() if now is None else now
    prev = int(existing or 0)
    return clock if clock > prev else prev + 1


def _content_text(skill: Skill) -> str:
    unversioned = skill.metadata.model_copy(update={"version": "", "created_at": None, "updated_at": None})
    return render_skill_md(skill.model_copy(update={"metadata": unversioned}))


def _command_arguments(doc: dict) -> list[CommandArgument]:
    raw = doc.get("commandArguments")
    return parse_arguments(json.loads(raw)) if isinstance(raw, str) and raw else []


def _candidate_label(doc: dict) -> dict[str, Any] | None:
    """A candidate's recorded label; present but unreadable reads as unknown (``{}``), never as none."""
    raw = doc.get("label")
    if raw is None:
        return None
    try:
        label = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        return {}
    return label if isinstance(label, dict) else {}


class SkillScope:
    """Who a skill is shared with (SLASH1 D4): only its author, one team, or the whole org."""

    PERSONAL = "personal"
    TEAM = "team"
    ORG = "org"
    SHARED = frozenset({"team", "org"})


# Storage columns a content edit carries forward unchanged: sharing is set by `share`, never by SKILL.md.
_SHARING_FIELDS = ("scope", "teamId", "authorFormer")


def _sharing_columns(doc: Mapping[str, Any]) -> dict[str, Any]:
    return {f: doc[f] for f in _SHARING_FIELDS if f in doc}


def _label_columns(doc: Mapping[str, Any], change_note: str | None) -> dict[str, Any]:
    """A new version's labels: the display name it keeps, and its own note (none: the last one's does not carry
    over, so it is written empty: an upsert that merges would otherwise keep it). Neo4j holds no null property."""
    columns: dict[str, Any] = {"changeNote": change_note or ""}
    if doc.get("displayName"):
        columns["displayName"] = doc["displayName"]
    return columns


@dataclass(frozen=True, slots=True)
class SkillSharing:
    """A stored skill's sharing: its scope, its team, and its author (``None`` once the author was erased, O3)."""

    scope: str
    team_id: str | None
    author_id: str | None
    author_former: bool

    @classmethod
    def of(cls, doc: Mapping[str, Any]) -> SkillSharing:
        scope = doc.get("scope") if doc.get("scope") in SkillScope.SHARED else SkillScope.PERSONAL
        return cls(
            scope=scope,
            team_id=doc.get("teamId") if scope == SkillScope.TEAM else None,
            author_id=doc.get("createdBy"),
            author_former=doc.get("authorFormer") is True,
        )


@dataclass(frozen=True, slots=True)
class SkillLabels:
    """What people read about a stored skill beside its content (UI-B4): the name they see, and the note its author
    gave the current version. Neither is content, so neither makes a version."""

    display_name: str | None
    change_note: str | None
    # UI-ADOPT2: the user id of whoever approved the proposal it came from (null: it was not a proposal). Kept apart
    # from `createdBy`, the learner who owns it, so an admin's approval never takes the skill.
    approved_by: str | None = None

    @classmethod
    def of(cls, doc: Mapping[str, Any]) -> SkillLabels:
        return cls(
            display_name=doc.get("displayName") or None,
            change_note=doc.get("changeNote") or None,
            approved_by=doc.get("approvedBy") or None,
        )


MAX_DISPLAY_NAME = 120


def _display_key(text: str) -> str:
    return " ".join(text.split()).casefold()


@dataclass(frozen=True, slots=True)
class SharedSkillRights:
    """Who manages a team or org skill: an org admin any of them, a team's owner their team's (CPD-11's roles)."""

    org_admin: bool = False
    owned_teams: frozenset[str] = frozenset()

    def manages(self, scope: str, team_id: str | None) -> bool:
        if scope == SkillScope.ORG:
            return self.org_admin
        if scope == SkillScope.TEAM:
            return self.org_admin or (team_id is not None and team_id in self.owned_teams)
        return False


def _doc_id(doc: dict) -> str | None:
    """Backend-agnostic identifier read — see module docstring."""
    return doc.get("id") or doc.get("_key")


_USAGE_FIELDS = (
    "usageTotalActivations", "usageSuccessfulOutcomes", "usageFailedOutcomes",
    "usageLastActivated", "usageFailureModes", "usageImprovementNotes",
)


def _default_usage() -> dict[str, Any]:
    return {
        "usageTotalActivations": 0,
        "usageSuccessfulOutcomes": 0,
        "usageFailedOutcomes": 0,
        "usageLastActivated": None,
        "usageFailureModes": [],
        "usageImprovementNotes": [],
    }


def _extract_usage(doc: dict) -> dict[str, Any]:
    """Pulls `GraphUsageTracker`'s counters off an existing `agentSkills`
    doc so a content edit (`update_skill`/`rollback`) can carry them
    forward into its full-document overwrite — see `_skill_to_doc`."""
    usage = _default_usage()
    for field in _USAGE_FIELDS:
        if field in doc and doc[field] is not None:
            usage[field] = doc[field]
    return usage


def _resources_to_fields(resources: dict[str, str]) -> dict[str, list[str]]:
    """`{path: content}` -> two index-aligned string arrays. Neo4j node
    properties can only be primitives or arrays thereof, so a nested map
    on the doc works on Arango but throws
    `Neo.ClientError.Statement.TypeError` on Neo4j — parallel arrays are
    the primitive representation both backends accept natively."""
    paths = sorted(resources)
    return {
        "resourcePaths": paths,
        "resourceContents": [resources[p] for p in paths],
    }


def _resources_from_doc(doc: dict) -> dict[str, str]:
    """Inverse of `_resources_to_fields`. Falls back to the legacy nested
    `resources` map for Arango docs written before the parallel-array
    encoding."""
    paths = doc.get("resourcePaths")
    if paths:
        contents = doc.get("resourceContents") or []
        return dict(zip(paths, contents))
    legacy = doc.get("resources")
    return dict(legacy) if isinstance(legacy, dict) else {}


def _resource_listing(resources: dict[str, str]) -> dict[str, list[str]]:
    """`{path: content}` -> `{kind: [paths]}`, matching the shape
    `Skill.resources` uses everywhere else (level-3 progressive
    disclosure — a listing, never eagerly-loaded content). A root-level
    path (no subdirectory) groups under the synthetic `"files"` kind —
    same convention as `loader.discover_resources` — since a real
    community skill can have reference files sitting directly beside
    SKILL.md (e.g. Anthropic's `pdf` skill's `forms.md`/`reference.md`),
    not only inside `scripts/`/`references/`/`assets/`."""
    listing: dict[str, list[str]] = {}
    for path in sorted(resources):
        kind = path.split("/", 1)[0] if "/" in path else "files"
        listing.setdefault(kind, []).append(path)
    return listing


def edge_source_key(edge: dict[str, Any]) -> str:
    """Extract an edge's source-node bare key, regardless of graph provider
    shape: ArangoDB's `get_edges_to_node` returns raw documents with a full
    `_from` id (`"collection/key"`), while Neo4j's returns a generic
    `from_id` that is already the bare key (see `Neo4jProvider.get_edges_to_node`).
    Returns "" if neither field is present/non-empty."""
    from_id = edge.get("from_id")
    if from_id:
        return str(from_id)
    return (edge.get("_from") or "").split("/", 1)[-1]


async def collect_referential_usage(
    graph: "IGraphDBProvider",
    org_id: str,
    name: str,
) -> SkillReferentialUsage:
    """Who currently depends on this skill — `AGENT_HAS_SKILL` assignments
    and `agentSkillRelation` `type == "requires"` edges. Shared by
    `GraphSkillStore.get_referential_usage` and the REST usage helper."""
    skill_full_id = f"{_SKILLS}/{org_id}_{name}"

    agent_edges = await graph.get_edges_to_node(skill_full_id, _AGENT_HAS_SKILL)
    used_by_agents: list[dict[str, Any]] = []
    for edge in agent_edges:
        agent_id = edge_source_key(edge)
        if not agent_id:
            continue
        agent_doc = await graph.get_document(agent_id, _AGENT_INSTANCES)
        if agent_doc:
            used_by_agents.append({"id": agent_id, "name": agent_doc.get("name", agent_id)})

    relation_edges = await graph.get_edges_to_node(skill_full_id, _RELATION)
    required_by_skills = sorted({
        edge_source_key(edge).removeprefix(f"{org_id}_")
        for edge in relation_edges
        if edge.get("type") == "requires" and edge_source_key(edge)
    })
    return SkillReferentialUsage(
        used_by_agents=used_by_agents,
        required_by_skills=required_by_skills,
    )


@dataclass(frozen=True, slots=True)
class SkillVisibility:
    """Which of an org's skills a reader sees: builtins always; a custom skill when one of ``viewers`` owns it, or
    when it is ``shared`` with the reader, which today means assigned to the agent they run (an assignment links
    only its editor's own skills). ``viewers=None`` reads every skill: only the builtin seeder and the reader of
    content a run already pinned use it.

    ``teams`` given (the REST API's reader, SLASH1 S4): org-scoped skills and those of these teams are seen too.
    Unset (the run-time reader), a team or org skill is seen only by its author."""

    viewers: frozenset[str] | None
    shared: frozenset[str] = frozenset()
    teams: frozenset[str] | None = None

    @classmethod
    def everyone(cls) -> SkillVisibility:
        return cls(None)

    @classmethod
    def of(
        cls, viewers: Iterable[str | None], shared: Iterable[str] = (), *, teams: Iterable[str] | None = None,
    ) -> SkillVisibility:
        """One principal can be stamped under several ids (the runtime writes its user id, the REST API and agent
        assignment the graph user key), so all of them are the viewer."""
        return cls(
            frozenset(v for v in viewers if v), frozenset(shared), None if teams is None else frozenset(teams),
        )

    def allows(self, doc: Mapping[str, Any]) -> bool:
        if self.viewers is None or doc.get("source") == SkillSource.BUILTIN.value:
            return True
        if doc.get("createdBy") in self.viewers or doc.get("name") in self.shared:
            return True
        if self.teams is None:
            return False
        sharing = SkillSharing.of(doc)
        return sharing.scope == SkillScope.ORG or (sharing.scope == SkillScope.TEAM and sharing.team_id in self.teams)


class GraphSkillStore(SkillStore, SkillHistoryReader, SkillCandidateStore):
    def __init__(
        self,
        graph_provider: "IGraphDBProvider",
        org_id: str,
        user_id: str,
        *,
        validator: SkillValidator | None = None,
        visibility: SkillVisibility | None = None,
        allow_builtin_writes: bool = False,
        rights: SharedSkillRights | None = None,
        provenance: Callable[[], SkillProvenance] | None = None,
    ) -> None:
        self._graph = graph_provider
        self._org_id = org_id
        self._user_id = user_id
        self._validator = validator or SkillValidator()
        # Unstated, a store reads as its acting user: an org-wide read must be asked for (CPS SK-4).
        self._visibility = visibility or SkillVisibility.of([user_id])
        self._allow_builtin_writes = allow_builtin_writes
        self._rights = rights or SharedSkillRights()
        # A run's store: every write is that run's model's, stamped with the run's label as it stands then.
        self._provenance = provenance

    def _key(self, name: str) -> str:
        return f"{self._org_id}_{name}"

    def _sees(self, doc: Mapping[str, Any]) -> bool:
        """Visible to the reader, or a shared skill they govern (an org admin sees every team's commands)."""
        if self._visibility.allows(doc):
            return True
        sharing = SkillSharing.of(doc)
        return sharing.scope in SkillScope.SHARED and self._rights.manages(sharing.scope, sharing.team_id)

    async def _get_writable_doc(self, name: str) -> dict | None:
        doc = await self._get_org_doc(name)
        if doc is None:
            return None
        if doc.get("source") == SkillSource.BUILTIN.value:
            if not self._allow_builtin_writes:
                raise RegistryError("Built-in skills are read-only")
        else:
            sharing = SkillSharing.of(doc)
            if sharing.scope in SkillScope.SHARED:
                # The role governs a shared skill, not authorship: its author's own agent cannot edit it either.
                if not self._rights.manages(sharing.scope, sharing.team_id):
                    raise RegistryError("Skill is not writable by this actor")
            elif doc.get("createdBy") != self._user_id:
                raise RegistryError("Skill is not writable by this actor")
        return doc

    def _validate_source(self, skill: Skill) -> None:
        if skill.metadata.source == SkillSource.BUILTIN and not self._allow_builtin_writes:
            raise RegistryError("Only the built-in seeder can write built-in skills")

    # ---- internal doc <-> domain-model mapping -----------------------------

    def _doc_to_metadata(self, doc: dict) -> SkillMetadata:
        """Fast path for listing: builds `SkillMetadata` straight from the
        denormalized doc fields, no SKILL.md parse. Kept in sync with
        `_skill_to_doc` by construction — both read/write the identical
        set of frontmatter fields."""
        return SkillMetadata(
            name=doc["name"],
            description=doc.get("description", ""),
            version=doc.get("version") or "1.0.0",
            category=doc.get("category"),
            subcategory=doc.get("subcategory"),
            tags=list(doc.get("tags") or []),
            concepts=list(doc.get("concepts") or []),
            related=list(doc.get("related") or []),
            requires=list(doc.get("requires") or []),
            status=SkillStatus(doc.get("status") or "active"),
            source=SkillSource(doc.get("source") or "manual"),
            created_at=str(doc["createdAtTimestamp"]) if doc.get("createdAtTimestamp") is not None else None,
            updated_at=str(doc["updatedAtTimestamp"]) if doc.get("updatedAtTimestamp") is not None else None,
            deprecated_reason=doc.get("deprecatedReason"),
            replaced_by=doc.get("replacedBy"),
            pack_name=doc.get("packName"),
            pack_version=doc.get("packVersion"),
            invocation_user=doc.get("invocationUser") is True,
            invocation_model=doc.get("invocationModel") is not False,
            arguments=_command_arguments(doc),
        )

    def _doc_to_skill(self, doc: dict) -> Skill:
        """Full body path: parses the stored `content` (always kept
        consistent with the denormalized fields — see `_skill_to_doc`) and
        overlays the resource *listing* derived from the stored
        `resourcePaths`/`resourceContents` arrays.

        Lifecycle fields (`updated_at`, status, deprecation) live on the
        graph document, not in SKILL.md — copy them onto metadata so
        GET /skills/:name can round-trip the If-Match token."""
        skill = parse_skill_md(doc["content"], expected_name=doc.get("name"), validator=self._validator)
        graph_meta = self._doc_to_metadata(doc)
        return skill.model_copy(update={
            "resources": _resource_listing(_resources_from_doc(doc)),
            "metadata": skill.metadata.model_copy(update={
                "created_at": graph_meta.created_at,
                "updated_at": graph_meta.updated_at,
                "status": graph_meta.status,
                "source": graph_meta.source,
                "version": graph_meta.version,
                "deprecated_reason": graph_meta.deprecated_reason,
                "replaced_by": graph_meta.replaced_by,
            }),
        })

    def _skill_to_doc(
        self,
        skill: Skill,
        *,
        resources: dict[str, str],
        created_by: str,
        created_at: int,
        updated_at: int,
        updated_by: str | None = None,
        usage: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """The single place a `Skill` becomes a document — `content` is
        ALWAYS re-rendered from `skill` here (never the caller's raw input
        string) specifically so the denormalized fields below and the full
        SKILL.md text can never drift apart.

        `usage` (the `GraphUsageTracker` counters — see `graph_tracker.py`)
        defaults to zeroed-out fresh values: every CRUD path here does a
        full-document `batch_upsert_nodes` overwrite, so a caller updating
        an EXISTING skill must pass `_extract_usage(existing_doc)` through,
        or that skill's accumulated usage history is silently wiped on
        every edit."""
        m = skill.metadata
        doc: dict[str, Any] = {
            "id": self._key(m.name),
            "orgId": self._org_id,
            "name": m.name,
            "description": m.description,
            "content": render_skill_md(skill),
            **_resources_to_fields(resources),
            "category": m.category,
            "subcategory": m.subcategory,
            "tags": list(m.tags),
            "concepts": list(m.concepts),
            "related": list(m.related),
            "requires": list(m.requires),
            "status": m.status.value,
            "source": m.source.value,
            "version": m.version,
            "deprecatedReason": m.deprecated_reason,
            "replacedBy": m.replaced_by,
            "packName": m.pack_name,
            "packVersion": m.pack_version,
            # Flat: a Neo4j property holds no maps, so the arguments travel as JSON text.
            "invocationUser": m.invocation_user,
            "invocationModel": m.invocation_model,
            "commandArguments": json.dumps([a.wire() for a in m.arguments]) if m.arguments else None,
            "createdBy": created_by,
            "updatedBy": updated_by,
            "createdAtTimestamp": created_at,
            "updatedAtTimestamp": updated_at,
        }
        doc.update(usage if usage is not None else _default_usage())
        return doc

    @staticmethod
    def _apply_category_defaults(skill: Skill, category: str | None, subcategory: str | None) -> Skill:
        """An explicit frontmatter value always wins — `category`/
        `subcategory` args are only a default for skills whose SKILL.md
        didn't declare them, matching `FilesystemSkillStore`'s precedence."""
        updates: dict[str, str] = {}
        if category is not None and skill.metadata.category is None:
            updates["category"] = category
        if subcategory is not None and skill.metadata.subcategory is None:
            updates["subcategory"] = subcategory
        if not updates:
            return skill
        return skill.model_copy(update={"metadata": skill.metadata.model_copy(update=updates)})

    def _stamped(
        self, skill: Skill, existing: dict | None, provenance: SkillProvenance | None,
    ) -> tuple[Skill, list[str]]:
        """``skill`` with the label its content now carries, and the conversations it derives from. Labels only
        rise: the stored skill's label, the one the content brings and the writing run's are joined, so neither
        an edit nor a rollback sheds what a run put in (FOLLOWUPS1-1)."""
        sessions = [str(s) for s in (existing or {}).get("sourceSessionIds") or []]
        labels = [skill.metadata.label]
        if existing is not None:
            labels.append(self._stored_label(existing))
        if provenance is not None:
            labels.append(provenance.label if provenance.label is not None else dict(UNKNOWN_LABEL))
            if provenance.session_id and provenance.session_id not in sessions:
                sessions.append(provenance.session_id)
        label = join_wire(labels)
        if label != skill.metadata.label:
            skill = skill.model_copy(update={"metadata": skill.metadata.model_copy(update={"label": label})})
        return skill, sessions

    def _stored_label(self, doc: dict) -> dict | None:
        try:
            stored = parse_skill_md(doc["content"], expected_name=doc.get("name"), validator=self._validator)
        except Exception:
            # Unparseable stored content cannot show it holds nothing a run wrote.
            return dict(UNKNOWN_LABEL) if doc.get("source") == SkillSource.AGENT_CREATED.value else None
        return content_label(stored.metadata)

    def _stamped_columns(self, doc: dict) -> dict[str, Any]:
        """A run's resource write changes no SKILL.md text, but its label and conversation go on the skill."""
        provenance = self._write_provenance()
        if provenance is None:
            return {}
        skill, sessions = self._stamped(self._doc_to_skill(doc), doc, provenance)
        return {"content": render_skill_md(skill), "sourceSessionIds": sessions}

    def _write_provenance(self) -> SkillProvenance | None:
        return self._provenance() if self._provenance is not None else None

    async def _get_org_doc(self, name: str) -> dict | None:
        doc = await self._graph.get_document(self._key(name), _SKILLS)
        if doc is None or org_of(doc) != self._org_id:
            return None
        if not self._sees(doc):
            return None
        return doc

    # ---- SkillReader --------------------------------------------------

    async def list_skills(self, filter: SkillFilter | None = None) -> list[SkillMetadata]:
        docs = await self._graph.get_nodes_by_filters(_SKILLS, {"orgId": self._org_id})
        docs = [d for d in docs if self._sees(d)]
        metadatas = [self._doc_to_metadata(d) for d in docs]
        if filter is not None:
            metadatas = [m for m in metadatas if matches_filter(m, filter)]
        return metadatas

    async def get_skill(self, name: str) -> Skill | None:
        doc = await self._get_org_doc(name)
        return self._doc_to_skill(doc) if doc is not None else None

    async def get_resource(self, skill_name: str, resource_path: str) -> str | None:
        doc = await self._get_org_doc(skill_name)
        if doc is None:
            return None
        return _resources_from_doc(doc).get(resource_path)

    async def get_resources(self, skill_name: str) -> dict[str, str]:
        """Overrides `SkillReader`'s default N+1 loop: one `get_document`
        call already fetches every `resourcePaths`/`resourceContents` pair
        for this skill, so there's nothing left to loop over — see
        `SkillBundleResolver`, the caller this exists for."""
        doc = await self._get_org_doc(skill_name)
        if doc is None:
            return {}
        return _resources_from_doc(doc)

    async def content_files(self, name: str, version: str | None = None) -> dict[str, str] | None:
        """`SKILL.md` and resources of the current content, or of `version`
        (current or archived), ignoring visibility scoping and deletion: a run
        pinned the content when it was granted, and the version log keeps
        what a deleted skill held. None for an unknown skill or version."""
        current = await self._graph.get_document(self._key(name), _SKILLS)
        if current is not None and org_of(current) != self._org_id:
            current = None
        if current is not None and version in (None, current.get("version") or "1.0.0"):
            doc = current
        elif version is None:
            return None
        else:
            doc = await self._get_version_doc(name, version)
        if doc is None:
            return None
        return {**_resources_from_doc(doc), "SKILL.md": doc.get("content") or ""}

    async def content_versions(self, name: str) -> list[str]:
        """Every version `content_files` can return, current first."""
        current = await self._graph.get_document(self._key(name), _SKILLS)
        versions: list[str] = []
        if current is not None and org_of(current) == self._org_id:
            versions.append(current.get("version") or "1.0.0")
        archived = await self._graph.get_nodes_by_filters(_VERSIONS, {"orgId": self._org_id, "name": name})
        for doc in sorted(archived, key=lambda d: int(d.get("createdAtTimestamp") or 0), reverse=True):
            version = doc.get("version") or "0.0.0"
            if version not in versions:
                versions.append(version)
        return versions

    async def in_force(self, name: str, agent_key: str | None, viewers: Iterable[str | None] = ()) -> bool:
        """Whether a run pinned to ``name`` may still use it: the skill exists and is not disabled, and it is still
        offered to the run (``skills.offer``): a builtin, assigned to the run's agent, or the run's person's own
        (``viewers``) while the agent assigns none. Deletion, disabling and unassignment narrow runs in flight (FB-11); an edit does not, since a
        run reads its pinned content from history."""
        key = self._key(name)
        doc = await self._graph.get_document(key, _SKILLS)
        if doc is None or org_of(doc) != self._org_id or doc.get("status") == SkillStatus.DISABLED.value:
            return False
        if agent_key is None or doc.get("source") == SkillSource.BUILTIN.value:
            return True
        edge = await self._graph.get_edge(
            from_id=agent_key, from_collection=_AGENT_INSTANCES, to_id=key, to_collection=_SKILLS,
            collection=_AGENT_HAS_SKILL,
        )
        if edge is not None:
            return True
        # The person's own skill is offered only while the agent assigns none: an assignment replaces them.
        if doc.get("createdBy") not in {v for v in viewers if v}:
            return False
        return not await self._graph.get_edges_from_node(f"{_AGENT_INSTANCES}/{agent_key}", _AGENT_HAS_SKILL)

    async def exists(self, name: str) -> bool:
        return await self._get_org_doc(name) is not None

    async def get_provenance(self, name: str) -> dict[str, Any] | None:
        """`{created_by, updated_by, pack_name, pack_version}` off the raw
        stored doc, or None if unknown — used by `BuiltinSkillSeeder` to
        detect whether an org has edited a builtin-sourced skill since it
        was last seeded. Deliberately not part of `SkillReader`: who
        wrote/touched a skill is a storage-provenance concern the portable
        `SkillMetadata`/`Skill` domain model has no business carrying."""
        doc = await self._get_org_doc(name)
        if doc is None:
            return None
        return {
            "created_by": doc.get("createdBy"),
            "updated_by": doc.get("updatedBy"),
            "pack_name": doc.get("packName"),
            "pack_version": doc.get("packVersion"),
        }

    async def sharing(self, name: str) -> SkillSharing | None:
        doc = await self._get_org_doc(name)
        return SkillSharing.of(doc) if doc is not None else None

    async def sharings(self) -> dict[str, SkillSharing]:
        """Every visible skill's sharing by name, in one read, for a listing."""
        docs = await self._graph.get_nodes_by_filters(_SKILLS, {"orgId": self._org_id})
        return {d["name"]: SkillSharing.of(d) for d in docs if self._sees(d)}

    async def share(self, name: str, scope: str, team_id: str | None) -> SkillSharing:
        """Publish a skill to a team or the org, or take it back to its author (SLASH1 D4 ``publish``). The caller
        must manage the skill as it is now and the scope it goes to; only its author may take a shared skill back
        to personal, since it becomes theirs alone."""
        doc = await self._get_writable_doc(name)
        if doc is None:
            raise RegistryError(f"Skill {name!r} not found")
        if doc.get("source") == SkillSource.BUILTIN.value:
            raise RegistryError("Built-in skills are read-only")
        if scope in SkillScope.SHARED:
            if not self._rights.manages(scope, team_id):
                raise RegistryError("Skill is not writable by this actor")
        elif doc.get("createdBy") != self._user_id:
            raise RegistryError("Skill is not writable by this actor")
        columns = {
            "scope": scope,
            "teamId": team_id if scope == SkillScope.TEAM else None,
            "updatedBy": self._user_id,
            "updatedAtTimestamp": _next_updated_at(doc.get("updatedAtTimestamp")),
        }
        await self._graph.update_node(self._key(name), _SKILLS, columns)
        return SkillSharing.of({**doc, **columns})

    async def labels(self) -> dict[str, SkillLabels]:
        """Every visible skill's labels by name, in one read, for a listing."""
        docs = await self._graph.get_nodes_by_filters(_SKILLS, {"orgId": self._org_id})
        return {d["name"]: SkillLabels.of(d) for d in docs if self._sees(d)}

    async def label(self, name: str) -> SkillLabels | None:
        doc = await self._get_org_doc(name)
        return SkillLabels.of(doc) if doc is not None else None

    async def rename(self, name: str, display_name: str | None, expected_updated_at: int | None = None) -> SkillLabels:
        """Give a skill the name people see (UI-B4); ``None`` goes back to its id. Its ``name`` stays: agents,
        installations, commands and history reach it by that, so nothing that names it changes. A name another skill
        of the org shows (its display name or, without one, its id) is refused, whoever can see that skill."""
        doc = await self._get_writable_doc(name)
        if doc is None:
            raise RegistryError(f"Skill {name!r} not found")
        if doc.get("source") == SkillSource.BUILTIN.value:
            raise RegistryError("Built-in skills are read-only")
        wanted = " ".join((display_name or "").split()) or None
        if wanted is not None and len(wanted) > MAX_DISPLAY_NAME:
            raise RegistryError(f"A skill name is at most {MAX_DISPLAY_NAME} characters")
        if wanted is not None:
            key = _display_key(wanted)
            for other in await self._graph.get_nodes_by_filters(_SKILLS, {"orgId": self._org_id}):
                if other.get("name") == name:
                    continue
                if key in {_display_key(str(other.get("displayName") or "")), _display_key(str(other.get("name") or ""))}:
                    raise RegistryError("Skill name is unavailable")
        if (doc.get("displayName") or None) == wanted:
            return SkillLabels.of(doc)
        observed = int(doc.get("updatedAtTimestamp") or 0)
        if expected_updated_at is not None and observed != expected_updated_at:
            raise SkillConflictError(name, current_updated_at=observed, current_version=doc.get("version"))
        merged = {
            **{k: v for k, v in doc.items() if k != "displayName"},
            **({"displayName": wanted} if wanted is not None else {}),
            "updatedBy": self._user_id,
            "updatedAtTimestamp": _next_updated_at(observed),
        }
        if not await self._graph.update_node_if_match(self._key(name), _SKILLS, merged, "updatedAtTimestamp", observed):
            current = await self._get_org_doc(name)
            raise SkillConflictError(
                name,
                current_updated_at=int((current or {}).get("updatedAtTimestamp") or 0),
                current_version=(current or {}).get("version"),
            )
        return SkillLabels.of(merged)

    # ---- SkillWriter ----------------------------------------------------

    def _validate_resources(self, resources: dict[str, str]) -> None:
        for path in resources:
            self._validator.validate_resource_path(path)
        self._validator.validate_resource_budget(resources)

    async def create_skill(
        self, name: str, content: str, category: str | None = None, subcategory: str | None = None,
        resources: dict[str, str] | None = None, *, provenance: SkillProvenance | None = None,
        change_note: str | None = None, approved_by: str | None = None,
    ) -> SkillMetadata:
        # Visibility-filtered existence must not turn a hidden record into an upsert.
        if await self._graph.get_document(self._key(name), _SKILLS) is not None:
            raise RegistryError("Skill name is unavailable")
        skill = parse_skill_md(content, expected_name=name, validator=self._validator)
        self._validator.validate_skill(skill, expected_name=name)
        self._validate_source(skill)
        skill = self._apply_category_defaults(skill, category, subcategory)
        resources = resources or {}
        self._validate_resources(resources)
        skill, sessions = self._stamped(skill, None, provenance or self._write_provenance())

        now = get_epoch_timestamp_in_ms()
        doc = self._skill_to_doc(
            skill, resources=resources, created_by=self._user_id, created_at=now, updated_at=now,
        )
        if sessions:
            doc["sourceSessionIds"] = sessions
        if change_note:
            doc["changeNote"] = change_note
        if approved_by:
            doc["approvedBy"] = approved_by
            doc["approvedAtTimestamp"] = now
        await self._graph.batch_upsert_nodes([doc], _SKILLS)
        await self._create_owner_permission_edge(name, now)
        await self._sync_relation_edges(name, skill.metadata, now)
        return skill.metadata

    async def update_skill(
        self,
        name: str,
        content: str,
        resources: dict[str, str] | None = None,
        expected_updated_at: int | None = None,
        status: SkillStatus | None = None,
        change_note: str | None = None,
    ) -> SkillMetadata:
        existing_doc = await self._get_writable_doc(name)
        if existing_doc is None:
            raise RegistryError(f"Skill {name!r} not found")
        if (
            expected_updated_at is not None
            and existing_doc.get("updatedAtTimestamp") != expected_updated_at
        ):
            raise SkillConflictError(
                name,
                current_updated_at=int(existing_doc.get("updatedAtTimestamp") or 0),
                current_version=existing_doc.get("version"),
            )
        skill = parse_skill_md(content, expected_name=name, validator=self._validator)
        self._validator.validate_skill(skill, expected_name=name)
        self._validate_source(skill)
        resolved_resources = _resources_from_doc(existing_doc) if resources is None else resources
        self._validate_resources(resolved_resources)
        skill, sessions = self._stamped(skill, existing_doc, self._write_provenance())
        if self._holds_content(existing_doc, skill, resolved_resources) and sessions == list(
            existing_doc.get("sourceSessionIds") or []
        ):
            return await self._keep_content(name, existing_doc, status, expected_updated_at)

        now = _next_updated_at(existing_doc.get("updatedAtTimestamp"))
        await self._snapshot_revision(existing_doc, now)
        bumped_metadata = skill.metadata.model_copy(update={"version": _bump_patch(existing_doc.get("version"))})
        skill = skill.model_copy(update={"metadata": bumped_metadata})
        doc = self._skill_to_doc(
            skill,
            resources=resolved_resources,
            created_by=existing_doc.get("createdBy", self._user_id),
            created_at=existing_doc.get("createdAtTimestamp", now),
            updated_at=now,
            updated_by=self._user_id,
            usage=_extract_usage(existing_doc),
        )
        doc.update(_sharing_columns(existing_doc))
        doc.update(_label_columns(existing_doc, change_note))
        if sessions:
            doc["sourceSessionIds"] = sessions
        # Pack SKILL.md is always `active`; lifecycle (e.g. a builtin mute)
        # lives on the graph column, so overlay it on this same CAS write
        # rather than a follow-up `set_skill_status`.
        if status is not None:
            doc["status"] = status.value
        await self._commit_skill_update(name, doc, expected_updated_at)
        await self._sync_relation_edges(name, skill.metadata, now)
        return skill.metadata

    async def patch_skill(
        self,
        name: str,
        old_string: str,
        new_string: str,
        expected_updated_at: int | None = None,
        change_note: str | None = None,
    ) -> bool:
        skill = await self.get_skill(name)
        if skill is None or skill.body.count(old_string) != 1:
            return False
        new_body = skill.body.replace(old_string, new_string, 1)
        self._validator.validate_body(new_body)
        await self.update_skill(
            name,
            render_skill_md(skill.model_copy(update={"body": new_body})),
            expected_updated_at=expected_updated_at,
            change_note=change_note,
        )
        return True

    async def delete_skill(self, name: str) -> bool:
        doc = await self._get_writable_doc(name)
        if doc is None:
            return False
        # Revision history in `agentSkillVersions` is intentionally left in
        # place after a hard-delete — it references this skill by
        # `skillKey`/`name`, not a live graph edge, and audit/compliance
        # value outlives the skill itself (same reasoning as an org's
        # audit log surviving deletion of the thing it describes). The last
        # content joins it, so runs pinned to it still resolve (P7.1).
        await self._snapshot_revision(doc, get_epoch_timestamp_in_ms())
        await self._graph.delete_nodes_and_edges([self._key(name)], _SKILLS)
        return True

    async def get_referential_usage(self, name: str) -> SkillReferentialUsage:
        return await collect_referential_usage(self._graph, self._org_id, name)

    async def detach_from_agents(self, name: str) -> None:
        usage = await self.get_referential_usage(name)
        if not usage.used_by_agents:
            return
        edges_to_delete = [
            {
                "from_id": agent["id"], "from_collection": _AGENT_INSTANCES,
                "to_id": f"{self._org_id}_{name}", "to_collection": _SKILLS,
            }
            for agent in usage.used_by_agents
        ]
        await self._graph.batch_delete_edges(edges_to_delete, _AGENT_HAS_SKILL)

    async def write_resource(self, skill_name: str, path: str, content: str) -> bool:
        self._validator.validate_resource_path(path)
        doc = await self._get_writable_doc(skill_name)
        if doc is None:
            return False
        resources = _resources_from_doc(doc)
        resources[path] = content
        self._validator.validate_resource_budget(resources)
        return await self._graph.update_node(
            self._key(skill_name), _SKILLS,
            {
                **_resources_to_fields(resources),
                **self._stamped_columns(doc),
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            },
        )

    async def remove_resource(self, skill_name: str, path: str) -> bool:
        doc = await self._get_writable_doc(skill_name)
        if doc is None:
            return False
        resources = _resources_from_doc(doc)
        if path not in resources:
            return False
        del resources[path]
        return await self._graph.update_node(
            self._key(skill_name), _SKILLS,
            {
                **_resources_to_fields(resources),
                **self._stamped_columns(doc),
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            },
        )

    async def deprecate_skill(self, name: str, reason: str, replaced_by: str | None = None) -> bool:
        skill = await self.get_skill(name)
        if skill is None:
            return False
        updated_metadata = skill.metadata.model_copy(update={
            "status": SkillStatus.DEPRECATED, "deprecated_reason": reason, "replaced_by": replaced_by,
        })
        updated_skill = skill.model_copy(update={"metadata": updated_metadata})
        await self.update_skill(name, render_skill_md(updated_skill))
        return True

    async def set_skill_status(
        self,
        name: str,
        status: SkillStatus,
        from_status: SkillStatus | None = None,
    ) -> bool:
        """Enable/disable primitive — writes only the `status` graph column
        (+ `updatedAtTimestamp`), unlike `deprecate_skill`'s `update_skill`
        round-trip: `_doc_to_skill` already overlays this column onto
        metadata regardless of what's parsed from `content` (see that
        method's docstring), so there is nothing in the stored SKILL.md text
        that needs to change and no revision/semver bump to make.

        `from_status` is compare-and-swap: the write lands only if the
        stored status still matches. `update_node_if_match` replaces the
        whole document, so the CAS carries the read doc with status and
        token overlaid — matching on `updatedAtTimestamp` so a concurrent
        content edit cannot be clobbered by a mute."""
        doc = await self._get_org_doc(name)
        if doc is None:
            return False
        if from_status is not None and doc.get("status") != from_status.value:
            return False
        observed = int(doc.get("updatedAtTimestamp") or 0)
        next_ts = _next_updated_at(observed)
        if from_status is None:
            return await self._graph.update_node(
                self._key(name), _SKILLS,
                {"status": status.value, "updatedAtTimestamp": next_ts},
            )
        merged = {**doc, "status": status.value, "updatedAtTimestamp": next_ts}
        return await self._graph.update_node_if_match(
            self._key(name), _SKILLS, merged, "updatedAtTimestamp", observed,
        )

    # ---- SkillHistoryReader ----------------------------------------------

    async def list_versions(self, name: str) -> list[SkillVersionInfo]:
        if await self._get_org_doc(name) is None:
            return []
        docs = await self._graph.get_nodes_by_filters(_VERSIONS, {"orgId": self._org_id, "name": name})
        versions = [
            SkillVersionInfo(
                version=d.get("version") or "0.0.0",
                updated_by=d.get("updatedBy"),
                created_at=str(d.get("createdAtTimestamp", "")),
                summary=d.get("summary") or "",
            )
            for d in docs
        ]
        versions.sort(key=lambda v: v.created_at, reverse=True)
        return versions

    async def get_version(self, name: str, version: str) -> Skill | None:
        if await self._get_org_doc(name) is None:
            return None
        archived = await self._get_version_doc(name, version)
        if archived is None:
            return None
        skill = parse_skill_md(archived["content"], expected_name=name, validator=self._validator)
        return skill.model_copy(update={"resources": _resource_listing(_resources_from_doc(archived))})

    async def rollback(self, name: str, version: str) -> SkillMetadata:
        current_doc = await self._get_writable_doc(name)
        if current_doc is None:
            raise RegistryError(f"Skill {name!r} not found")
        archived = await self._get_version_doc(name, version)
        if archived is None:
            raise RegistryError(f"Version {version!r} of skill {name!r} not found")

        restored = parse_skill_md(archived["content"], expected_name=name, validator=self._validator)
        self._validate_source(restored)
        # The archived text's own label too: a learned version from before labels is unknown, not unlabelled.
        restored = restored.model_copy(
            update={"metadata": restored.metadata.model_copy(update={"label": content_label(restored.metadata)})}
        )
        restored, sessions = self._stamped(restored, current_doc, self._write_provenance())
        if self._holds_content(current_doc, restored, _resources_from_doc(archived)) and sessions == list(
            current_doc.get("sourceSessionIds") or []
        ):
            return self._doc_to_metadata(current_doc)

        now = get_epoch_timestamp_in_ms()
        await self._snapshot_revision(current_doc, now)
        # Rollback creates a NEW revision on top of the current one (never
        # reuses the archived version number) — history stays monotonic and
        # a second rollback can always distinguish "restored copy" from
        # "the original".
        bumped_metadata = restored.metadata.model_copy(update={"version": _bump_patch(current_doc.get("version"))})
        restored = restored.model_copy(update={"metadata": bumped_metadata})
        doc = self._skill_to_doc(
            restored,
            resources=_resources_from_doc(archived),
            created_by=current_doc.get("createdBy", self._user_id),
            created_at=current_doc.get("createdAtTimestamp", now),
            updated_at=now,
            updated_by=self._user_id,
            usage=_extract_usage(current_doc),
        )
        doc.update(_sharing_columns(current_doc))
        doc.update(_label_columns(current_doc, f"Restored version {version}"))
        if sessions:
            doc["sourceSessionIds"] = sessions
        await self._graph.batch_upsert_nodes([doc], _SKILLS)
        await self._sync_relation_edges(name, restored.metadata, now)
        return restored.metadata

    async def _commit_skill_update(
        self,
        name: str,
        doc: dict[str, Any],
        expected_updated_at: int | None,
    ) -> None:
        """Last-write-wins when `expected_updated_at` is None; otherwise the
        timestamp check is the write itself (`update_node_if_match`), not a
        prior read. The written token always advances past the matched
        value so a same-millisecond clock cannot reuse If-Match."""
        incoming = int(doc.get("updatedAtTimestamp") or 0)
        if expected_updated_at is not None:
            doc["updatedAtTimestamp"] = (
                incoming if incoming > expected_updated_at else expected_updated_at + 1
            )
        if expected_updated_at is None:
            await self._graph.batch_upsert_nodes([doc], _SKILLS)
            return
        applied = await self._graph.update_node_if_match(
            self._key(name),
            _SKILLS,
            doc,
            "updatedAtTimestamp",
            expected_updated_at,
        )
        if applied:
            return
        current = await self._get_org_doc(name)
        if current is None:
            raise RegistryError(f"Skill {name!r} not found")
        raise SkillConflictError(
            name,
            current_updated_at=int(current.get("updatedAtTimestamp") or 0),
            current_version=current.get("version"),
        )

    def _holds_content(self, doc: dict, skill: Skill, resources: dict[str, str]) -> bool:
        """Whether ``doc`` already stores ``skill``'s text and ``resources``: a version names content, so the
        same content is never a new one (EV3 FW-7). The version and timestamps written into the text are the
        store's own bookkeeping, not content."""
        if _resources_from_doc(doc) != resources:
            return False
        try:
            stored = parse_skill_md(doc["content"], expected_name=skill.metadata.name, validator=self._validator)
        except Exception:
            return False
        return _content_text(stored) == _content_text(skill)

    async def _keep_content(
        self, name: str, doc: dict, status: SkillStatus | None, expected_updated_at: int | None,
    ) -> SkillMetadata:
        """An update that changes no content: only a different lifecycle status is written, under the same
        If-Match as a content write."""
        if status is not None and doc.get("status") != status.value:
            doc = {**doc, "status": status.value, "updatedAtTimestamp": _next_updated_at(doc.get("updatedAtTimestamp"))}
            await self._commit_skill_update(name, doc, expected_updated_at)
        return self._doc_to_metadata(doc)

    async def _get_version_doc(self, name: str, version: str) -> dict | None:
        docs = await self._graph.get_nodes_by_filters(
            _VERSIONS, {"orgId": self._org_id, "name": name, "version": version},
        )
        return docs[0] if docs else None

    async def _snapshot_revision(self, current_doc: dict, now: int) -> None:
        """Archives `current_doc` into `agentSkillVersions` before it's
        overwritten by the caller's `update_skill`/`rollback`.

        The doc `id` is keyed on `(skill_key, version)`, NOT `(skill_key,
        now)`: `now` is an epoch-millisecond timestamp, and two snapshot
        calls landing in the same millisecond (trivially reproducible in a
        fast test, and not impossible under real concurrent/rapid edits)
        would collide and silently overwrite each other via
        `batch_upsert_nodes`'s id-based upsert — losing a whole revision.
        `version` is safe here specifically because it's monotonically
        bumped and never reused (see `rollback`'s docstring), so it's a
        stronger uniqueness key than a timestamp for this skill's history.
        """
        skill_key = _doc_id(current_doc) or self._key(current_doc["name"])
        version = current_doc.get("version") or "1.0.0"
        version_doc = {
            "id": f"{skill_key}_v{version}",
            "orgId": self._org_id,
            "skillKey": skill_key,
            "name": current_doc["name"],
            "version": version,
            "content": current_doc.get("content", ""),
            **_resources_to_fields(_resources_from_doc(current_doc)),
            # The note its author gave this version as it was saved (UI-B4).
            "summary": current_doc.get("changeNote") or "",
            "updatedBy": current_doc.get("updatedBy") or current_doc.get("createdBy"),
            "createdAtTimestamp": now,
        }
        await self._graph.batch_upsert_nodes([version_doc], _VERSIONS)

    # ---- Graph edges: permissions + skill-to-skill connectedness --------

    async def _create_owner_permission_edge(self, name: str, now: int) -> None:
        edge = {
            "from_id": self._user_id,
            "from_collection": _USERS,
            "to_id": self._key(name),
            "to_collection": _SKILLS,
            "type": "USER",
            "role": "OWNER",
            "createdAtTimestamp": now,
            "updatedAtTimestamp": now,
        }
        await self._graph.batch_create_edges([edge], _PERMISSION)

    async def _sync_relation_edges(self, name: str, metadata: SkillMetadata, now: int) -> None:
        """Re-derives every `agentSkillRelation` edge from `metadata`'s
        `related`/`requires`/`replaced_by` — called on every create/update
        so the graph never drifts from the frontmatter that's supposed to
        describe it. Simplest correct approach for a catalog this size:
        drop and recreate, rather than diffing."""
        from_key = self._key(name)
        await self._graph.delete_edges_from(from_key, _SKILLS, _RELATION)

        edges: list[dict[str, Any]] = []
        for related_name in metadata.related:
            edges.append({
                "from_id": from_key, "from_collection": _SKILLS,
                "to_id": self._key(related_name), "to_collection": _SKILLS,
                "type": "related", "createdAtTimestamp": now,
            })
        for required_name in metadata.requires:
            edges.append({
                "from_id": from_key, "from_collection": _SKILLS,
                "to_id": self._key(required_name), "to_collection": _SKILLS,
                "type": "requires", "createdAtTimestamp": now,
            })
        if metadata.replaced_by:
            edges.append({
                "from_id": from_key, "from_collection": _SKILLS,
                "to_id": self._key(metadata.replaced_by), "to_collection": _SKILLS,
                "type": "replaced_by", "createdAtTimestamp": now,
            })
        if edges:
            await self._graph.batch_create_edges(edges, _RELATION)

    # ---- SkillCandidateStore ----------------------------------------------

    async def queue_candidate(self, candidate: SkillCandidate) -> None:
        provenance = self._write_provenance()
        label = candidate.label
        session_id = candidate.source_session_id
        if provenance is not None:
            label = join_wire([label, provenance.label if provenance.label is not None else dict(UNKNOWN_LABEL)])
            session_id = session_id or provenance.session_id
        doc = {
            "id": candidate.candidate_id,
            "orgId": self._org_id,
            "candidateId": candidate.candidate_id,
            "name": candidate.name,
            "description": candidate.description,
            "body": candidate.body,
            "category": candidate.category,
            "subcategory": candidate.subcategory,
            "tags": list(candidate.tags),
            "status": candidate.status,
            "sourceTrajectorySummary": candidate.source_trajectory_summary,
            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            # Neo4j stores no null properties: a candidate learned outside a conversation has no field.
            **({"sessionId": session_id} if session_id else {}),
            # JSON text: a Neo4j property holds no maps. Approval carries it onto the skill (FOLLOWUPS1-1).
            **({"label": json.dumps(label, sort_keys=True)} if label is not None else {}),
            **({"agentKey": candidate.source_agent_key} if candidate.source_agent_key else {}),
            **({"runId": candidate.source_run_id} if candidate.source_run_id else {}),
            # The person the learning run acted for: the candidate's owner-to-be, who may decide it.
            "proposedBy": candidate.proposed_by or self._user_id,
        }
        await self._graph.batch_upsert_nodes([doc], _CANDIDATES)

    async def get_pending_candidates(self) -> list[SkillCandidate]:
        docs = await self._graph.get_nodes_by_filters(_CANDIDATES, {"orgId": self._org_id})
        return [
            SkillCandidate(
                candidate_id=d.get("candidateId") or _doc_id(d),
                name=d["name"],
                description=d.get("description", ""),
                body=d.get("body", ""),
                category=d.get("category"),
                subcategory=d.get("subcategory"),
                tags=list(d.get("tags") or []),
                source_trajectory_summary=d.get("sourceTrajectorySummary") or "",
                created_at=str(d.get("createdAtTimestamp", "")),
                status=d.get("status") or "pending",
                source_session_id=d.get("sessionId"),
                label=_candidate_label(d),
                source_agent_key=d.get("agentKey") or None,
                source_run_id=d.get("runId") or None,
                proposed_by=d.get("proposedBy") or None,
            )
            for d in docs
        ]

    async def remove_candidate(self, candidate_id: str) -> None:
        await self._graph.delete_nodes([candidate_id], _CANDIDATES)


SESSION_CANDIDATES_PAGE = 50


async def erase_session_skill_candidates(
    graph: IGraphDBProvider, org_id: str, session_id: str, *, page: int = SESSION_CANDIDATES_PAGE,
) -> tuple[int, bool]:
    """Up to one page of the skill candidates learned from a conversation, erased with it (CLEANUP-G7): their body and
    trajectory summary are drawn from its content. Returns ``(erased, remaining)``. Idempotent."""
    docs = await graph.get_documents_paginated(
        _CANDIDATES,
        skip=0,
        limit=page + 1,
        filters={"orgId": org_id, "sessionId": session_id},
        sort_field="_key",
        raise_on_error=True,
    )
    keys = [_doc_id(d) for d in docs[:page]]
    keys = [k for k in keys if k]
    if keys:
        await graph.delete_nodes(keys, _CANDIDATES)
    return len(keys), len(docs) > page


async def erase_session_learned_skills(
    graph: IGraphDBProvider, org_id: str, session_id: str, *, page: int = SESSION_CANDIDATES_PAGE,
) -> tuple[int, bool]:
    """Up to one page of the skills a conversation's runs wrote (FOLLOWUPS1-1), erased with it with their version
    log, whatever their scope and whoever edited them since: their text derives from it, as a memory derived from a
    session goes with the session (P7.3). Returns ``(erased, remaining)``. Idempotent."""
    docs = [
        d for d in await graph.get_nodes_by_filters(_SKILLS, {"orgId": org_id})
        if session_id in (d.get("sourceSessionIds") or [])
    ]
    erased = 0
    for doc in docs[:page]:
        key = _doc_id(doc)
        if key is None:
            continue
        versions = await graph.get_nodes_by_filters(_VERSIONS, {"orgId": org_id, "name": doc["name"]})
        version_keys = [k for k in (_doc_id(v) for v in versions) if k]
        if version_keys:
            await graph.delete_nodes(version_keys, _VERSIONS)
        await graph.delete_nodes_and_edges([key], _SKILLS)
        erased += 1
    return erased, len(docs) > page


async def erase_org_skill_candidates(
    graph: IGraphDBProvider, org_id: str, *, page: int = SESSION_CANDIDATES_PAGE,
) -> tuple[int, bool]:
    """Up to one page of every skill candidate of an erased org (CLEANUP-G1), named or not. ``(erased, remaining)``."""
    docs = await graph.get_documents_paginated(
        _CANDIDATES, skip=0, limit=page + 1, filters={"orgId": org_id}, sort_field="_key", raise_on_error=True,
    )
    keys = [k for k in (_doc_id(d) for d in docs[:page]) if k]
    if keys:
        await graph.delete_nodes(keys, _CANDIDATES)
    return len(keys), len(docs) > page


async def erase_skill_author(
    graph: IGraphDBProvider, org_id: str, author_ids: Iterable[str | None],
) -> tuple[int, int]:
    """A deleted user's skills (SLASH1 D4, O3): personal ones are erased with their version log; team and org ones
    pass to the org, their author shown as a former member. Their id is also cleared from every ``updatedBy``.
    Idempotent, so a redelivered deletion event is harmless. Returns ``(erased, reassigned)``."""
    ids = {i for i in author_ids if i}
    if not ids:
        return 0, 0
    erased = reassigned = 0
    for doc in await graph.get_nodes_by_filters(_SKILLS, {"orgId": org_id}):
        key = _doc_id(doc)
        if key is None or doc.get("source") == SkillSource.BUILTIN.value:
            continue
        if doc.get("createdBy") not in ids:
            if doc.get("updatedBy") in ids:
                await graph.update_node(key, _SKILLS, {"updatedBy": None})
            continue
        if SkillSharing.of(doc).scope == SkillScope.PERSONAL:
            versions = await graph.get_nodes_by_filters(_VERSIONS, {"orgId": org_id, "name": doc["name"]})
            version_keys = [k for k in (_doc_id(v) for v in versions) if k]
            if version_keys:
                await graph.delete_nodes(version_keys, _VERSIONS)
            await graph.delete_nodes_and_edges([key], _SKILLS)
            erased += 1
            continue
        await graph.batch_delete_edges(
            [{"from_id": a, "from_collection": _USERS, "to_id": key, "to_collection": _SKILLS} for a in ids],
            _PERMISSION,
        )
        await graph.update_node(key, _SKILLS, {
            "createdBy": None,
            "authorFormer": True,
            **({"updatedBy": None} if doc.get("updatedBy") in ids else {}),
        })
        reassigned += 1
    for version in await graph.get_nodes_by_filters(_VERSIONS, {"orgId": org_id}):
        key = _doc_id(version)
        if key is not None and version.get("updatedBy") in ids:
            await graph.update_node(key, _VERSIONS, {"updatedBy": None})
    return erased, reassigned
