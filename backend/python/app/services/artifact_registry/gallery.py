"""HTTP-facing artifact gallery: display policy, listing, and metadata.

Reads only. Writes stay on ``ArtifactRegistryService``. Graph queries go
through ``IGraphDBProvider`` so this module stays store-agnostic.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.entities import ArtifactRecord, ArtifactType, ArtifactVisibility, deserialize_artifact_versions
from app.modules.authz.chat_content_access import ChatContentAccess, get_chat_content_access
from app.services.artifact_registry.access import ArtifactNotFoundError
from app.services.artifact_registry.models import Actor
from app.utils.user_lookup import get_caller_user

__all__ = [
    "ALLOWED_GALLERY_ARTIFACT_TYPES",
    "ALLOWED_GALLERY_OWNERSHIP",
    "ALLOWED_GALLERY_SORT_FIELDS",
    "ArtifactDisplayPolicy",
    "ArtifactGalleryService",
    "ArtifactGalleryVersion",
    "ArtifactDetail",
    "ArtifactListItem",
    "ArtifactListPage",
    "ArtifactListQuery",
]

ALLOWED_GALLERY_SORT_FIELDS = frozenset(
    {"name", "createdAtTimestamp", "updatedAtTimestamp", "artifactType"}
)
ALLOWED_GALLERY_ARTIFACT_TYPES = frozenset(
    t.value for t in ArtifactType if t is not ArtifactType.TOOL_RESULT
)
ALLOWED_GALLERY_OWNERSHIP = frozenset({"all", "mine", "shared"})


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


class ArtifactDisplayPolicy:
    """Stateless user-facing visibility rules. Graph queries enforce the
    same filters; this is defense in depth for get-by-id and stream."""

    @staticmethod
    def is_user_visible(
        *,
        artifact_type: Any = None,
        is_temporary: bool | None = False,
        visibility: Any = None,
    ) -> bool:
        if _enum_value(artifact_type) == ArtifactType.TOOL_RESULT.value:
            return False
        if is_temporary is True:
            return False
        vis = _enum_value(visibility)
        if vis is not None and vis != ArtifactVisibility.VISIBLE.value:
            return False
        return True

    @classmethod
    def is_user_visible_doc(cls, artifact_doc: dict) -> bool:
        return cls.is_user_visible(
            artifact_type=artifact_doc.get("artifactType"),
            is_temporary=artifact_doc.get("isTemporary"),
            visibility=artifact_doc.get("visibility"),
        )

    @classmethod
    def is_user_visible_record(cls, record: ArtifactRecord) -> bool:
        return cls.is_user_visible(
            artifact_type=record.artifact_type,
            is_temporary=record.is_temporary,
            visibility=record.visibility,
        )


class ArtifactListQuery(BaseModel):
    search: str | None = None
    artifact_types: list[str] | None = None
    conversation_id: str | None = None
    date_from: int | None = None
    date_to: int | None = None
    sort_by: str = "createdAtTimestamp"
    sort_order: str = "desc"
    ownership: str = "all"
    page: int = 1
    limit: int = 50


class ArtifactGalleryVersion(BaseModel):
    model_config = ConfigDict(populate_by_name=True, ser_json_by_alias=True)

    version: int
    size_bytes: int = Field(default=0, serialization_alias="sizeBytes")
    content_hash: str = Field(default="", serialization_alias="contentHash")
    created_at: int = Field(default=0, serialization_alias="createdAt")


class ArtifactListItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True, ser_json_by_alias=True)

    artifact_id: str = Field(serialization_alias="artifactId")
    name: str
    logical_name: str | None = Field(default=None, serialization_alias="logicalName")
    artifact_type: str = Field(serialization_alias="artifactType")
    mime_type: str | None = Field(default=None, serialization_alias="mimeType")
    size_in_bytes: int | None = Field(default=None, serialization_alias="sizeInBytes")
    version: int
    content_hash: str | None = Field(default=None, serialization_alias="contentHash")
    conversation_id: str | None = Field(default=None, serialization_alias="conversationId")
    created_at: int | None = Field(default=None, serialization_alias="createdAt")
    updated_at: int | None = Field(default=None, serialization_alias="updatedAt")
    role: str | None = None


class ArtifactDetail(ArtifactListItem):
    versions: list[ArtifactGalleryVersion] = Field(default_factory=list)
    description: str = ""
    source_tool: str | None = Field(default=None, serialization_alias="sourceTool")


class ArtifactListPage(BaseModel):
    model_config = ConfigDict(populate_by_name=True, ser_json_by_alias=True)

    items: list[ArtifactListItem]
    page: int
    limit: int
    total_count: int = Field(serialization_alias="totalCount")
    total_pages: int = Field(serialization_alias="totalPages")
    counts: dict[str, int] = Field(default_factory=dict)


class ArtifactGalleryService:
    def __init__(self, graph: Any, chat_access: ChatContentAccess | None = None) -> None:
        self._graph = graph
        self._chat_access = chat_access

    async def list(self, actor: Actor, query: ArtifactListQuery) -> ArtifactListPage:
        user_key = await self._resolve_user_key(actor)
        sort_by = query.sort_by if query.sort_by in ALLOWED_GALLERY_SORT_FIELDS else "createdAtTimestamp"
        sort_order = "asc" if (query.sort_order or "").lower() == "asc" else "desc"
        artifact_types = self._sanitize_types(query.artifact_types)
        ownership = query.ownership if query.ownership in ALLOWED_GALLERY_OWNERSHIP else "all"
        page = max(query.page, 1)
        limit = min(max(query.limit, 1), 100)
        skip = (page - 1) * limit
        rows, role_counts = await self._graph.list_accessible_artifacts(
            user_id=user_key,
            org_id=actor.org_id,
            skip=skip,
            limit=limit,
            search=query.search,
            artifact_types=artifact_types,
            conversation_id=query.conversation_id,
            date_from=query.date_from,
            date_to=query.date_to,
            sort_by=sort_by,
            sort_order=sort_order,
            ownership=ownership,
        )
        mine = int(role_counts.get("mine") or 0)
        shared = int(role_counts.get("shared") or 0)
        counts = {"all": mine + shared, "mine": mine, "shared": shared}
        total = counts[ownership]
        items: list[ArtifactListItem] = []
        for row in rows or []:
            newest = await self._newest_readable(actor, row)
            if newest is None:
                continue
            items.append(self._to_list_item(row, at=newest))
        total_pages = (total + limit - 1) // limit if limit else 0
        return ArtifactListPage(
            items=items,
            page=page,
            limit=limit,
            total_count=total,
            total_pages=total_pages,
            counts=counts,
        )

    async def get(self, actor: Actor, artifact_id: str) -> ArtifactDetail:
        user_key = await self._resolve_user_key(actor)
        row = await self._graph.get_artifact_detail(user_key, actor.org_id, artifact_id)
        if not row:
            raise ArtifactNotFoundError(f"Artifact not found: {artifact_id}")
        artifact_doc = row.get("artifactDoc") or {}
        if not ArtifactDisplayPolicy.is_user_visible_doc(artifact_doc):
            raise ArtifactNotFoundError(f"Artifact not found: {artifact_id}")
        readable = await self._readable_versions(actor, row)
        if readable is not None and not readable:
            raise ArtifactNotFoundError(f"Artifact not found: {artifact_id}")
        return self._to_detail(row, readable)

    async def list_versions(self, actor: Actor, artifact_id: str) -> list[ArtifactGalleryVersion]:
        detail = await self.get(actor, artifact_id)
        return detail.versions

    def _access(self) -> ChatContentAccess:
        return self._chat_access or get_chat_content_access()

    def _all_versions_granted(self, row: dict) -> bool:
        """Whether the reader's own edge on the row opens every version: their OWNER
        edge, or any edge where graph edges grant chat content. Anyone else (a direct
        share, where they do not) opens what the installed access lets them."""
        role = (row.get("permission") or {}).get("role")
        return role == "OWNER" or self._access().graph_grants_chat_content

    async def _readable_versions(self, actor: Actor, row: dict) -> list[int] | None:
        """The versions of the row's artifact the reader may open, oldest first;
        None when they may open every one."""
        if self._all_versions_granted(row):
            return None
        record_id = str(row.get("id") or "")
        if not record_id:
            return []
        return await self._access().readable_chat_versions(
            self._graph, user_id=actor.user_id, org_id=actor.org_id, record=record_id,
        )

    async def _newest_readable(self, actor: Actor, row: dict) -> int | None:
        """The version a listing shows the reader: the current one when they may open
        it, else the newest they may open; None when they may open none."""
        current = int(row.get("version") or 1)
        if self._all_versions_granted(row):
            return current
        record_id = str(row.get("id") or "")
        if not record_id:
            return None
        if await self._access().can_read_chat_content(
            self._graph, user_id=actor.user_id, org_id=actor.org_id, record=record_id, version=current,
        ):
            return current
        readable = await self._readable_versions(actor, row)
        return readable[-1] if readable else None

    async def _resolve_user_key(self, actor: Actor) -> str:
        user = await get_caller_user(self._graph, actor.user_id)
        if not user:
            raise ArtifactNotFoundError("User not found")
        user_key = user.get("_key") or user.get("id")
        if not user_key:
            raise ArtifactNotFoundError("User not found")
        return user_key

    @staticmethod
    def _sanitize_types(artifact_types: list[str] | None) -> list[str] | None:
        if artifact_types is None:
            return None
        return [t for t in artifact_types if t in ALLOWED_GALLERY_ARTIFACT_TYPES]

    @staticmethod
    def _versions_from_doc(artifact_doc: dict, current_version: int) -> list[ArtifactGalleryVersion]:
        raw = deserialize_artifact_versions(artifact_doc.get("versions"))
        versions: list[ArtifactGalleryVersion] = []
        for entry in raw:
            registry_version = entry.get("registryVersion")
            if registry_version is None:
                continue
            versions.append(
                ArtifactGalleryVersion(
                    version=int(registry_version),
                    size_bytes=int(entry.get("sizeBytes") or 0),
                    content_hash=str(entry.get("contentHash") or ""),
                    created_at=int(entry.get("createdAt") or 0),
                )
            )
        if not versions:
            versions.append(
                ArtifactGalleryVersion(
                    version=current_version or 1,
                    size_bytes=int(artifact_doc.get("sizeInBytes") or 0),
                    content_hash=str(artifact_doc.get("contentHash") or ""),
                    created_at=0,
                )
            )
        versions.sort(key=lambda v: v.version)
        return versions

    @classmethod
    def _to_list_item(cls, row: dict, *, at: int | None = None) -> ArtifactListItem:
        """The row as a listing shows it; `at` an older version than the current one
        describes the file as of that version, for a reader who may open no later one."""
        art = row.get("artifactDoc") or {}
        current = int(row.get("version") or 1)
        item = ArtifactListItem(
            artifact_id=str(row.get("id") or ""),
            name=art.get("name") or row.get("recordName") or "",
            logical_name=art.get("logicalName"),
            artifact_type=art.get("artifactType") or "OTHER",
            mime_type=art.get("mimeType") or row.get("mimeType"),
            size_in_bytes=art.get("sizeInBytes") if art.get("sizeInBytes") is not None else row.get("sizeInBytes"),
            version=current,
            content_hash=art.get("contentHash"),
            conversation_id=art.get("conversationId"),
            created_at=row.get("createdAtTimestamp"),
            updated_at=row.get("updatedAtTimestamp"),
            role=(row.get("permission") or {}).get("role"),
        )
        if at is None or at == current:
            return item
        shown = next((v for v in cls._versions_from_doc(art, current) if v.version == at), None)
        entry = next((e for e in deserialize_artifact_versions(art.get("versions")) if e.get("registryVersion") == at), {})
        return item.model_copy(update={
            "version": at,
            "mime_type": entry.get("mimeType") or item.mime_type,
            "size_in_bytes": shown.size_bytes if shown else None,
            "content_hash": (shown.content_hash or None) if shown else None,
            "updated_at": (shown.created_at or row.get("createdAtTimestamp")) if shown else row.get("createdAtTimestamp"),
        })

    @classmethod
    def _to_detail(cls, row: dict, readable: list[int] | None = None) -> ArtifactDetail:
        """The row with its versions; `readable` (None: every one) the versions the
        reader may open, the newest of which the detail describes."""
        art = row.get("artifactDoc") or {}
        current = int(row.get("version") or 1)
        item = cls._to_list_item(row, at=readable[-1] if readable else None)
        versions = cls._versions_from_doc(art, current)
        if readable is not None:
            allowed = set(readable)
            versions = [v for v in versions if v.version in allowed]
        return ArtifactDetail(
            **item.model_dump(),
            versions=versions,
            description=art.get("description") or "",
            source_tool=art.get("sourceTool"),
        )
