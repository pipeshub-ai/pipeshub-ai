"""HTTP-facing artifact gallery: display policy, listing, and metadata.

Reads only. Writes stay on ``ArtifactRegistryService``. Graph queries go
through ``IGraphDBProvider`` so this module stays store-agnostic.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.entities import ArtifactRecord, ArtifactType, ArtifactVisibility, deserialize_artifact_versions
from app.services.artifact_registry.access import ArtifactNotFoundError
from app.services.artifact_registry.models import Actor

__all__ = [
    "ALLOWED_GALLERY_ARTIFACT_TYPES",
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


class ArtifactGalleryService:
    def __init__(self, graph: Any) -> None:
        self._graph = graph

    async def list(self, actor: Actor, query: ArtifactListQuery) -> ArtifactListPage:
        user_key = await self._resolve_user_key(actor)
        sort_by = query.sort_by if query.sort_by in ALLOWED_GALLERY_SORT_FIELDS else "createdAtTimestamp"
        sort_order = "asc" if (query.sort_order or "").lower() == "asc" else "desc"
        artifact_types = self._sanitize_types(query.artifact_types)
        page = max(query.page, 1)
        limit = min(max(query.limit, 1), 100)
        skip = (page - 1) * limit
        rows, total = await self._graph.list_accessible_artifacts(
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
        )
        items = [self._to_list_item(row) for row in rows or []]
        total_pages = (total + limit - 1) // limit if limit else 0
        return ArtifactListPage(
            items=items,
            page=page,
            limit=limit,
            total_count=total,
            total_pages=total_pages,
        )

    async def get(self, actor: Actor, artifact_id: str) -> ArtifactDetail:
        user_key = await self._resolve_user_key(actor)
        row = await self._graph.get_artifact_detail(user_key, actor.org_id, artifact_id)
        if not row:
            raise ArtifactNotFoundError(f"Artifact not found: {artifact_id}")
        artifact_doc = row.get("artifactDoc") or {}
        if not ArtifactDisplayPolicy.is_user_visible_doc(artifact_doc):
            raise ArtifactNotFoundError(f"Artifact not found: {artifact_id}")
        return self._to_detail(row)

    async def list_versions(self, actor: Actor, artifact_id: str) -> list[ArtifactGalleryVersion]:
        detail = await self.get(actor, artifact_id)
        return detail.versions

    async def _resolve_user_key(self, actor: Actor) -> str:
        user = await self._graph.get_user_by_user_id(actor.user_id)
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
    def _to_list_item(cls, row: dict) -> ArtifactListItem:
        art = row.get("artifactDoc") or {}
        return ArtifactListItem(
            artifact_id=str(row.get("id") or ""),
            name=art.get("name") or row.get("recordName") or "",
            logical_name=art.get("logicalName"),
            artifact_type=art.get("artifactType") or "OTHER",
            mime_type=art.get("mimeType") or row.get("mimeType"),
            size_in_bytes=art.get("sizeInBytes") if art.get("sizeInBytes") is not None else row.get("sizeInBytes"),
            version=int(row.get("version") or 1),
            content_hash=art.get("contentHash"),
            conversation_id=art.get("conversationId"),
            created_at=row.get("createdAtTimestamp"),
            updated_at=row.get("updatedAtTimestamp"),
        )

    @classmethod
    def _to_detail(cls, row: dict) -> ArtifactDetail:
        item = cls._to_list_item(row)
        art = row.get("artifactDoc") or {}
        return ArtifactDetail(
            **item.model_dump(),
            versions=cls._versions_from_doc(art, item.version),
            description=art.get("description") or "",
            source_tool=art.get("sourceTool"),
        )
