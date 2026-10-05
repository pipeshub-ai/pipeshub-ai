"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/artifacts."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import requests

from helper.http.api_client import APIClient
from helper.second_user import SecondUser

if TYPE_CHECKING:
    from helper.graph_provider import GraphProviderProtocol

ARTIFACTS_BASE = "/api/v1/artifacts"
MISSING_ARTIFACT_ID = "00000000-0000-4000-8000-000000000000"
# The route only accepts ^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$.
MALFORMED_ARTIFACT_ID = "not.path-safe"
OVERLONG_ARTIFACT_ID = "a" * 129

# Node's validator list; Python additionally knows OTHER and TOOL_RESULT.
GALLERY_ARTIFACT_TYPES = (
    "CODE_OUTPUT",
    "CHART",
    "DOCUMENT",
    "IMAGE",
    "SPREADSHEET",
    "PRESENTATION",
    "DATA_FILE",
    "CODE",
)
GALLERY_SORT_FIELDS = ("name", "createdAtTimestamp", "updatedAtTimestamp", "artifactType")


class ArtifactsClient(APIClient):
    """Client for /api/v1/artifacts, acting as the shared org admin."""

    BASE = ARTIFACTS_BASE

    def list(self, *, auth: bool = True, **params: Any) -> requests.Response:
        return self.get("", auth=auth, params=params)

    def get_one(self, artifact_id: str, *, auth: bool = True) -> requests.Response:
        return self.get(f"/{artifact_id}", auth=auth)

    def versions(self, artifact_id: str, *, auth: bool = True) -> requests.Response:
        return self.get(f"/{artifact_id}/versions", auth=auth)


def request_as(user: SecondUser, path: str = "", **kwargs: Any) -> requests.Response:
    """GET an artifacts route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    return requests.get(
        f"{user.base_url}{ARTIFACTS_BASE}{path}", headers=user.headers, **kwargs
    )


@dataclass(frozen=True)
class SeededArtifact:
    """What ``seed_artifact`` wrote, as the gallery is expected to report it."""

    artifact_id: str
    owner_key: str
    name: str
    artifact_type: str
    mime_type: str
    conversation_id: str | None
    version: int
    size_in_bytes: int
    content_hash: str
    created_at: int
    version_numbers: tuple[int, ...]


async def seed_artifact(
    graph: "GraphProviderProtocol",
    *,
    org_id: str,
    owner_key: str,
    version_count: int = 2,
    **overrides: Any,
) -> SeededArtifact:
    """Write one artifact the way ``VersionManager.create`` does, minus the blob.

    No route creates an artifact without an agent run, and the gallery routes read
    only the graph. ``owner_key`` is the graph user key; ``overrides`` are
    ``ArtifactRecord`` fields (``artifact_type``, ``visibility``, ``is_temporary``,
    ``conversation_id``, ``record_name`` ...).
    """
    from app.config.constants.arangodb import (  # noqa: PLC0415 - needs backend on sys.path
        CollectionNames,
        Connectors,
        OriginTypes,
    )
    from app.models.entities import ArtifactRecord, ArtifactType, RecordType  # noqa: PLC0415
    from app.utils.time_conversion import get_epoch_timestamp_in_ms  # noqa: PLC0415

    artifact_id = str(uuid.uuid4())
    now = get_epoch_timestamp_in_ms()
    tag = uuid.uuid4().hex
    size = 64
    versions = [
        {
            "registryVersion": number,
            "storageVersion": number - 1,
            "contentHash": f"{number:064x}",
            "sizeBytes": size + number,
            "createdAt": now + number,
        }
        for number in range(1, version_count + 1)
    ]
    fields: dict[str, Any] = {
        "id": artifact_id,
        "org_id": org_id,
        "record_name": f"spec-audit-{tag[:8]}.md",
        "record_type": RecordType.ARTIFACT,
        "external_record_id": f"spec-audit-{tag}",
        "version": version_count,
        "origin": OriginTypes.UPLOAD,
        "connector_name": Connectors.CODING_SANDBOX,
        "connector_id": f"{Connectors.CODING_SANDBOX.value.lower()}_{org_id}",
        "mime_type": "text/markdown",
        "size_in_bytes": size + version_count,
        "created_at": now,
        "updated_at": now,
        "indexing_status": "NOT_STARTED",
        "extraction_status": "NOT_STARTED",
        "preview_renderable": True,
        "hide_weburl": True,
        "artifact_type": ArtifactType.DOCUMENT,
        "description": "Seeded by the artifacts spec audit",
        "source_tool": "spec_audit.seed",
        # A well-formed ObjectId with no chat behind it: filters the list to this row
        # and leaves conversationTitle absent.
        "conversation_id": tag[:24],
        "content_hash": f"{version_count:064x}",
        "versions": versions,
        **overrides,
    }
    record = ArtifactRecord(**fields)

    records = CollectionNames.RECORDS.value
    artifacts = CollectionNames.ARTIFACTS.value
    await graph.batch_upsert_nodes([record.to_arango_base_record()], records)
    await graph.batch_upsert_nodes([record.to_arango_artifact_record()], artifacts)
    await graph.batch_create_edges(
        [
            {
                "from_id": owner_key,
                "from_collection": CollectionNames.USERS.value,
                "to_id": record.id,
                "to_collection": records,
                "type": "USER",
                "role": "OWNER",
                "createdAtTimestamp": now,
                "updatedAtTimestamp": now,
            }
        ],
        CollectionNames.PERMISSION.value,
    )
    await graph.batch_create_edges(
        [
            {
                "from_id": record.id,
                "from_collection": records,
                "to_id": record.id,
                "to_collection": artifacts,
                "createdAtTimestamp": now,
                "updatedAtTimestamp": now,
            }
        ],
        CollectionNames.IS_OF_TYPE.value,
    )
    return SeededArtifact(
        artifact_id=record.id,
        owner_key=owner_key,
        name=record.record_name,
        artifact_type=record.artifact_type.value,
        mime_type=record.mime_type,
        conversation_id=record.conversation_id,
        version=record.version,
        size_in_bytes=record.size_in_bytes or 0,
        content_hash=record.content_hash or "",
        created_at=record.created_at,
        version_numbers=tuple(v["registryVersion"] for v in record.versions),
    )


async def remove_artifact(graph: "GraphProviderProtocol", seeded: SeededArtifact) -> None:
    """Delete everything ``seed_artifact`` wrote for one artifact."""
    from app.config.constants.arangodb import CollectionNames  # noqa: PLC0415

    users = CollectionNames.USERS.value
    records = CollectionNames.RECORDS.value
    artifacts = CollectionNames.ARTIFACTS.value
    key = seeded.artifact_id
    await graph.delete_edge(seeded.owner_key, users, key, records, CollectionNames.PERMISSION.value)
    await graph.delete_edge(key, records, key, artifacts, CollectionNames.IS_OF_TYPE.value)
    await graph.delete_nodes([key], artifacts)
    await graph.delete_nodes([key], records)
