"""`ArangoHTTPProvider.clone_agent_template` through the provider's real
`get_document` → `_translate_node_from_arango` and `batch_upsert_nodes` →
`_translate_node_to_arango` path. Only the HTTP client is replaced, by a
store that keeps documents by `_key` and overwrites on insert, as Arango does
with `overwrite=True`."""

from __future__ import annotations

import copy
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

TEMPLATES = "agentTemplates"


class _DocumentStore:
    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict[str, Any]]] = {}

    async def get_document(self, collection: str, key: str, txn_id: str | None = None,
                           raise_on_error: bool = False) -> dict | None:
        doc = self.collections.get(collection, {}).get(key)
        return copy.deepcopy(doc) if doc is not None else None

    async def batch_insert_documents(self, collection: str, documents: list[dict],
                                     txn_id: str | None = None, overwrite: bool = False) -> dict:
        stored = self.collections.setdefault(collection, {})
        for doc in documents:
            key = doc["_key"]
            if key in stored and not overwrite:
                return {"errors": 1}
            stored[key] = {**copy.deepcopy(doc), "_id": f"{collection}/{key}", "_rev": "_new"}
        return {"errors": 0}


def _provider(store: _DocumentStore) -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(spec=logging.Logger), AsyncMock())
    provider.http_client = store
    return provider


async def test_copy_is_a_new_document_and_the_source_is_untouched() -> None:
    store = _DocumentStore()
    source = {
        "_key": "src", "_id": f"{TEMPLATES}/src", "_rev": "_abc", "name": "Onboarding",
        "createdBy": "k-alice", "createdAtTimestamp": 1, "updatedAtTimestamp": 2, "isDeleted": False,
    }
    store.collections[TEMPLATES] = {"src": copy.deepcopy(source)}

    new_id = await _provider(store).clone_agent_template("src")

    assert new_id and new_id != "src"
    assert store.collections[TEMPLATES]["src"] == source
    copy_doc = store.collections[TEMPLATES][new_id]
    assert copy_doc["name"] == "Onboarding"
    assert copy_doc["createdBy"] is None
    assert copy_doc["_id"] == f"{TEMPLATES}/{new_id}"
    assert "id" not in copy_doc


async def test_missing_source_copies_nothing() -> None:
    store = _DocumentStore()
    assert await _provider(store).clone_agent_template("nope") is None
    assert store.collections == {}
