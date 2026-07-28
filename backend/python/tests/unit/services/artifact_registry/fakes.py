"""In-memory `IGraphDBProvider`/`BlobStorage` test doubles shared by every
`artifact_registry` unit test module — one implementation so a bug in the
fake itself surfaces consistently everywhere instead of being fixed
piecemeal per test file (DRY)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.schema.node_validator import NodeSchemaValidator

_PRIMITIVES = (str, int, float, bool, type(None))
_VALIDATOR = NodeSchemaValidator()


def assert_valid_write(collection: str, doc: dict[str, Any]) -> None:
    """Apply BOTH gates a real write passes: Neo4j's property-type rules
    and the collection's declared JSON schema.

    Checking only the first is how the `versions` bump broke twice — a
    list-of-dicts failed Neo4j's type rule, and JSON-encoding it to satisfy
    that then failed the schema, which still declared `array`. The fake
    enforced one gate, so both regressions passed their tests.
    """
    assert_storable_properties(collection, doc)
    _VALIDATOR.validate_node_update(collection, doc)


def assert_storable_properties(collection: str, doc: dict[str, Any]) -> None:
    """Reject property values Neo4j cannot store.

    Neo4j allows only primitives and arrays of primitives as node
    properties; a dict or a list-of-dicts raises `CypherTypeError` at
    write time. ArangoDB stores both natively, so a fake that accepts
    anything makes every test pass while production explodes on half the
    supported backends — which is exactly how a list-of-dicts `versions`
    write reached a live Neo4j deployment and broke every artifact
    version bump. Enforcing the stricter of the two backends here keeps
    that honest.
    """
    for key, value in doc.items():
        if isinstance(value, _PRIMITIVES):
            continue
        if isinstance(value, (list, tuple)) and all(isinstance(v, _PRIMITIVES) for v in value):
            continue
        raise TypeError(
            f"{collection}.{key} is not storable on Neo4j: property values must be "
            f"primitives or arrays of primitives, got {type(value).__name__} ({value!r}). "
            "Serialize it (see `entities.serialize_artifact_versions`)."
        )


class FakeGraphProvider:
    """Minimal in-memory stand-in for `IGraphDBProvider`. Backend-agnostic
    on purpose — the artifact_registry package only calls the generic
    `get_document`/`batch_upsert_nodes`/`get_edge`/etc. methods that exist
    identically on both the ArangoDB and Neo4j providers, so exercising
    against this fake is representative of both. Writes go through
    `assert_valid_write` so "works against the fake" cannot mean "works on
    Arango only" or "storable but schema-invalid"."""

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
            assert_valid_write(collection, doc)
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
        assert_valid_write(collection, updates)
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
    tests can assert on stored content without touching Mongo/S3.

    `documents[id]["versions"]` is a legacy simple "content history" list
    kept for older tests that just assert a count. `documents[id]["version_history"]`
    mimics Node's REAL `versionHistory` semantics (lazy v0 on first bump,
    0-indexed) so `upload_artifact_version`'s `storageVersion`/
    `priorStorageVersion` return values — and therefore
    `get_document_version_history`/version-pinned `get_buffer` below — are
    representative of production behaviour (see `storage.controller.ts`'s
    `uploadNextVersionDocument`)."""

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
            "content_type": content_type, "versions": [file_bytes], "version_history": [],
        }
        return {"documentId": document_id}

    async def upload_artifact_version(
        self, *, org_id: str, document_id: str, file_name: str, file_bytes: bytes, content_type: str,
    ) -> dict[str, Any]:
        doc = self.documents[document_id]
        version_history: list[bytes] = doc.setdefault("version_history", [])
        prior_storage_version = None
        if not version_history:
            # Lazy v0: Node snapshots the pre-existing "current" content
            # the first time a document is ever bumped.
            version_history.append(doc["content"])
            prior_storage_version = 0
        version_history.append(file_bytes)
        storage_version = len(version_history) - 1

        doc["content"] = file_bytes
        doc["content_type"] = content_type
        doc["versions"].append(file_bytes)
        return {
            "documentId": document_id,
            "sizeBytes": len(file_bytes),
            "storageVersion": storage_version,
            "priorStorageVersion": prior_storage_version,
        }

    async def get_document_version_history(self, org_id: str, document_id: str) -> list[dict]:
        version_history = self.documents[document_id].get("version_history") or []
        return [{"version": i} for i in range(len(version_history))]

    async def get_buffer(self, org_id: str, document_id: str, version: int | None = None) -> bytes:
        """Test-only helper mirroring the real `/buffer` route: `version`
        indexes into the storage layer's `version_history`, not the
        registry's version numbering."""
        doc = self.documents[document_id]
        if version is None:
            return doc["content"]
        return doc["version_history"][version]

    async def get_direct_upload_url(self, org_id: str, document_id: str) -> str:
        return f"https://blob.example/upload/{document_id}"

    async def get_download_url(self, org_id: str, document_id: str, version: int | None = None) -> str:
        suffix = f"?version={version}" if version is not None else ""
        return f"https://blob.example/download/{document_id}{suffix}"
