"""`get_nodes_by_field_in(collection, "id", ...)` must match on ArangoDB.

ArangoDB is the DEFAULT datastore (`DATA_STORE` defaults to "arangodb"), and
`_translate_node_to_arango` moves `id` into `_key` on write -- so `id` is not a
stored attribute and `FILTER doc.id IN [...]` matched nothing. Every batched
lookup added for throughput (blob_storage, retrieval_service, chat_helpers)
passes field="id", so on the default backend they silently returned empty
batches: citations lost their links and linked-record context disappeared, with
no error anywhere.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider


def _provider() -> ArangoHTTPProvider:
    p = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    p.logger = MagicMock()
    p.http_client = MagicMock()
    p.http_client.execute_aql = AsyncMock(return_value=[])
    return p


def _aql(provider: ArangoHTTPProvider) -> str:
    return provider.http_client.execute_aql.await_args.args[0]


class TestIdIsTranslatedToKey:
    @pytest.mark.asyncio
    async def test_id_filters_on_key(self) -> None:
        p = _provider()
        await p.get_nodes_by_field_in("records", "id", ["a", "b"])
        query = _aql(p)
        assert "FILTER doc._key IN @values" in query
        assert "doc.id IN" not in query, "`id` is not stored; this matches nothing"

    @pytest.mark.asyncio
    async def test_other_fields_are_untouched(self) -> None:
        p = _provider()
        await p.get_nodes_by_field_in("records", "virtualRecordId", ["v1"])
        assert "FILTER doc.virtualRecordId IN @values" in _aql(p)

    @pytest.mark.asyncio
    async def test_projected_id_comes_from_key(self) -> None:
        """A caller asking for `id` back must get it, or it cannot key the
        results to the ids it requested."""
        p = _provider()
        await p.get_nodes_by_field_in("records", "id", ["a"], return_fields=["id", "webUrl"])
        query = _aql(p)
        assert "id: doc._key" in query
        assert "webUrl: doc.webUrl" in query
