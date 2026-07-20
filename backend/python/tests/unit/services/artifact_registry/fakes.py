"""In-memory `IGraphDBProvider`/`BlobStorage` test doubles shared by every
`artifact_registry` unit test module — one implementation so a bug in the
fake itself surfaces consistently everywhere instead of being fixed
piecemeal per test file (DRY)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


class FakeGraphProvider:
    """Minimal in-memory stand-in for `IGraphDBProvider`. Backend-agnostic
    on purpose — the artifact_registry package only calls the generic
    `get_document`/`batch_upsert_nodes`/`get_edge`/etc. methods that exist
    identically on both the ArangoDB and Neo4j providers, so exercising
    against this fake is representative of both."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, dict]] = defaultdict(dict)
        self.edges: dict[str, list[dict]] = defaultdict(list)
        self.users: dict[str, dict] = {}

    def add_user(self, user_id: str, *, key: str | None = None) -> None:
        self.users[user_id] = {"_key": key or user_id, "userId": user_id}

    async def get_user_by_user_id(self, user_id: str) -> dict | None:
        return self.users.get(user_id)

    async def get_document(self, doc_id: str, collection: str) -> dict | None:
        return self.nodes[collection].get(doc_id)

    async def batch_upsert_nodes(self, docs: list[dict], collection: str) -> bool:
        for doc in docs:
            key = doc.get("_key") or doc.get("id")
            self.nodes[collection][key] = dict(doc)
        return True

    async def batch_create_edges(self, edges: list[dict], collection: str) -> bool:
        self.edges[collection].extend(dict(e) for e in edges)
        return True

    async def get_edge(
        self, *, from_id: str, from_collection: str, to_id: str, to_collection: str, collection: str,
    ) -> dict | None:
        for edge in self.edges[collection]:
            if edge["from_id"] == from_id and edge["to_id"] == to_id:
                return edge
        return None

    async def update_node(self, doc_id: str, collection: str, updates: dict[str, Any]) -> bool:
        doc = self.nodes[collection].setdefault(doc_id, {})
        doc.update(updates)
        return True

    async def get_documents_paginated(
        self, collection: str, *, skip: int = 0, limit: int = 100, filters: dict[str, Any] | None = None,
    ) -> list[dict]:
        filters = filters or {}
        matches = [
            doc for doc in self.nodes[collection].values()
            if all(doc.get(k) == v for k, v in filters.items())
        ]
        return matches[skip:skip + limit]

    async def get_edges_from_node(self, node_ref: str, collection: str) -> list[dict]:
        node_id = node_ref.split("/", 1)[1]
        return [e for e in self.edges[collection] if e["from_id"] == node_id]

    async def get_edges_to_node(self, node_ref: str, collection: str) -> list[dict]:
        node_id = node_ref.split("/", 1)[1]
        return [e for e in self.edges[collection] if e["to_id"] == node_id]


class FakeBlobStore:
    """Stand-in for `app.modules.transformers.blob_storage.BlobStorage` —
    tracks uploaded bytes per `documentId` so `VersionManager`/registry
    tests can assert on stored content without touching Mongo/S3."""

    def __init__(self) -> None:
        self.config_service = object()
        self._next_id = 0
        self.documents: dict[str, dict[str, Any]] = {}

    def _new_document_id(self) -> str:
        self._next_id += 1
        return f"doc-{self._next_id}"

    async def save_versioned_artifact_to_storage(
        self, *, org_id: str, conversation_id: str, file_name: str, file_bytes: bytes, content_type: str,
    ) -> dict[str, Any]:
        document_id = self._new_document_id()
        self.documents[document_id] = {
            "org_id": org_id, "file_name": file_name, "content": file_bytes,
            "content_type": content_type, "versions": [file_bytes],
        }
        return {"documentId": document_id}

    async def upload_artifact_version(
        self, *, org_id: str, document_id: str, file_name: str, file_bytes: bytes, content_type: str,
    ) -> dict[str, Any]:
        doc = self.documents[document_id]
        doc["content"] = file_bytes
        doc["content_type"] = content_type
        doc["versions"].append(file_bytes)
        return {"documentId": document_id}

    async def get_direct_upload_url(self, org_id: str, document_id: str) -> str:
        return f"https://blob.example/upload/{document_id}"

    async def get_download_url(self, org_id: str, document_id: str) -> str:
        return f"https://blob.example/download/{document_id}"
