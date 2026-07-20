"""`GraphSkillStore`: agent_loop_lib's `SkillStore` + `SkillHistoryReader` +
`SkillCandidateStore` backed by `IGraphDBProvider` — works unmodified
against either `ArangoHTTPProvider` or `Neo4jProvider` since it only calls
the generic, backend-agnostic surface of that interface (`get_document`,
`batch_upsert_nodes`, `update_node`, `delete_nodes`/`delete_nodes_and_edges`,
`batch_create_edges`, `delete_edges_from`, `get_nodes_by_filters`) — never
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

import re
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.base import (
    Skill,
    SkillCandidate,
    SkillFilter,
    SkillMetadata,
    SkillSource,
    SkillStatus,
    SkillVersionInfo,
    matches_filter,
)
from app.agent_loop_lib.modules.providers.skills.loader import parse_skill_md, render_skill_md
from app.agent_loop_lib.modules.providers.skills.store import (
    SkillCandidateStore,
    SkillHistoryReader,
    SkillStore,
)
from app.agent_loop_lib.modules.providers.skills.validator import SkillValidator
from app.config.constants.arangodb import CollectionNames
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["GraphSkillStore"]

_SKILLS = CollectionNames.AGENT_SKILLS.value
_VERSIONS = CollectionNames.AGENT_SKILL_VERSIONS.value
_CANDIDATES = CollectionNames.AGENT_SKILL_CANDIDATES.value
_RELATION = CollectionNames.AGENT_SKILL_RELATION.value
_PERMISSION = CollectionNames.PERMISSION.value
_USERS = CollectionNames.USERS.value

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
    disclosure — a listing, never eagerly-loaded content)."""
    listing: dict[str, list[str]] = {}
    for path in sorted(resources):
        kind = path.split("/", 1)[0]
        listing.setdefault(kind, []).append(path)
    return listing


class GraphSkillStore(SkillStore, SkillHistoryReader, SkillCandidateStore):
    def __init__(
        self,
        graph_provider: "IGraphDBProvider",
        org_id: str,
        user_id: str,
        *,
        validator: SkillValidator | None = None,
    ) -> None:
        self._graph = graph_provider
        self._org_id = org_id
        self._user_id = user_id
        self._validator = validator or SkillValidator()

    def _key(self, name: str) -> str:
        return f"{self._org_id}_{name}"

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
        )

    def _doc_to_skill(self, doc: dict) -> Skill:
        """Full body path: parses the stored `content` (always kept
        consistent with the denormalized fields — see `_skill_to_doc`) and
        overlays the resource *listing* derived from the stored
        `resourcePaths`/`resourceContents` arrays."""
        skill = parse_skill_md(doc["content"], expected_name=doc.get("name"), validator=self._validator)
        return skill.model_copy(update={"resources": _resource_listing(_resources_from_doc(doc))})

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

    async def _get_org_doc(self, name: str) -> dict | None:
        doc = await self._graph.get_document(self._key(name), _SKILLS)
        if doc is None or doc.get("orgId") != self._org_id:
            return None
        return doc

    # ---- SkillReader --------------------------------------------------

    async def list_skills(self, filter: SkillFilter | None = None) -> list[SkillMetadata]:
        docs = await self._graph.get_nodes_by_filters(_SKILLS, {"orgId": self._org_id})
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

    # ---- SkillWriter ----------------------------------------------------

    async def create_skill(
        self, name: str, content: str, category: str | None = None, subcategory: str | None = None,
    ) -> SkillMetadata:
        if await self.exists(name):
            raise RegistryError(f"Skill {name!r} already exists")
        skill = parse_skill_md(content, expected_name=name, validator=self._validator)
        self._validator.validate_skill(skill, expected_name=name)
        skill = self._apply_category_defaults(skill, category, subcategory)

        now = get_epoch_timestamp_in_ms()
        doc = self._skill_to_doc(skill, resources={}, created_by=self._user_id, created_at=now, updated_at=now)
        await self._graph.batch_upsert_nodes([doc], _SKILLS)
        await self._create_owner_permission_edge(name, now)
        await self._sync_relation_edges(name, skill.metadata, now)
        return skill.metadata

    async def update_skill(self, name: str, content: str) -> SkillMetadata:
        existing_doc = await self._get_org_doc(name)
        if existing_doc is None:
            raise RegistryError(f"Skill {name!r} not found")
        skill = parse_skill_md(content, expected_name=name, validator=self._validator)
        self._validator.validate_skill(skill, expected_name=name)

        now = get_epoch_timestamp_in_ms()
        await self._snapshot_revision(existing_doc, now)
        bumped_metadata = skill.metadata.model_copy(update={"version": _bump_patch(existing_doc.get("version"))})
        skill = skill.model_copy(update={"metadata": bumped_metadata})
        doc = self._skill_to_doc(
            skill,
            resources=_resources_from_doc(existing_doc),
            created_by=existing_doc.get("createdBy", self._user_id),
            created_at=existing_doc.get("createdAtTimestamp", now),
            updated_at=now,
            updated_by=self._user_id,
            usage=_extract_usage(existing_doc),
        )
        await self._graph.batch_upsert_nodes([doc], _SKILLS)
        await self._sync_relation_edges(name, skill.metadata, now)
        return skill.metadata

    async def patch_skill(self, name: str, old_string: str, new_string: str) -> bool:
        skill = await self.get_skill(name)
        if skill is None or skill.body.count(old_string) != 1:
            return False
        new_body = skill.body.replace(old_string, new_string, 1)
        self._validator.validate_body(new_body)
        await self.update_skill(name, render_skill_md(skill.model_copy(update={"body": new_body})))
        return True

    async def delete_skill(self, name: str) -> bool:
        if await self._get_org_doc(name) is None:
            return False
        # Revision history in `agentSkillVersions` is intentionally left in
        # place after a hard-delete — it references this skill by
        # `skillKey`/`name`, not a live graph edge, and audit/compliance
        # value outlives the skill itself (same reasoning as an org's
        # audit log surviving deletion of the thing it describes).
        await self._graph.delete_nodes_and_edges([self._key(name)], _SKILLS)
        return True

    async def write_resource(self, skill_name: str, path: str, content: str) -> bool:
        doc = await self._get_org_doc(skill_name)
        if doc is None:
            return False
        resources = _resources_from_doc(doc)
        resources[path] = content
        return await self._graph.update_node(
            self._key(skill_name), _SKILLS,
            {**_resources_to_fields(resources), "updatedAtTimestamp": get_epoch_timestamp_in_ms()},
        )

    async def remove_resource(self, skill_name: str, path: str) -> bool:
        doc = await self._get_org_doc(skill_name)
        if doc is None:
            return False
        resources = _resources_from_doc(doc)
        if path not in resources:
            return False
        del resources[path]
        return await self._graph.update_node(
            self._key(skill_name), _SKILLS,
            {**_resources_to_fields(resources), "updatedAtTimestamp": get_epoch_timestamp_in_ms()},
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

    # ---- SkillHistoryReader ----------------------------------------------

    async def list_versions(self, name: str) -> list[SkillVersionInfo]:
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
        archived = await self._get_version_doc(name, version)
        if archived is None:
            return None
        skill = parse_skill_md(archived["content"], expected_name=name, validator=self._validator)
        return skill.model_copy(update={"resources": _resource_listing(_resources_from_doc(archived))})

    async def rollback(self, name: str, version: str) -> SkillMetadata:
        current_doc = await self._get_org_doc(name)
        if current_doc is None:
            raise RegistryError(f"Skill {name!r} not found")
        archived = await self._get_version_doc(name, version)
        if archived is None:
            raise RegistryError(f"Version {version!r} of skill {name!r} not found")

        now = get_epoch_timestamp_in_ms()
        await self._snapshot_revision(current_doc, now)

        restored = parse_skill_md(archived["content"], expected_name=name, validator=self._validator)
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
        await self._graph.batch_upsert_nodes([doc], _SKILLS)
        await self._sync_relation_edges(name, restored.metadata, now)
        return restored.metadata

    async def _get_version_doc(self, name: str, version: str) -> dict | None:
        docs = await self._graph.get_nodes_by_filters(
            _VERSIONS, {"orgId": self._org_id, "name": name, "version": version},
        )
        return docs[0] if docs else None

    async def _snapshot_revision(self, current_doc: dict, now: int) -> None:
        skill_key = _doc_id(current_doc) or self._key(current_doc["name"])
        version_doc = {
            "id": f"{skill_key}_{now}",
            "orgId": self._org_id,
            "skillKey": skill_key,
            "name": current_doc["name"],
            "version": current_doc.get("version") or "1.0.0",
            "content": current_doc.get("content", ""),
            **_resources_to_fields(_resources_from_doc(current_doc)),
            "summary": "",
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
            )
            for d in docs
        ]

    async def remove_candidate(self, candidate_id: str) -> None:
        await self._graph.delete_nodes([candidate_id], _CANDIDATES)
