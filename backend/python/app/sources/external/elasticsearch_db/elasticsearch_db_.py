# ruff: noqa
from __future__ import annotations

from elasticsearch import Elasticsearch  # type: ignore[import-untyped]
from typing import Dict, List, Optional, Union, cast

from app.sources.client.elasticsearch_db.elasticsearch_db import ElasticsearchResponse

class ElasticsearchDataSource:
    """
    Strict, typed wrapper over elasticsearch-py for common Elasticsearch operations.

    Accepts either an elasticsearch-py `Elasticsearch` instance *or* any object with `.get_sdk() -> Elasticsearch`.
    """

    def __init__(self, client_or_sdk: Union[Elasticsearch, object]) -> None:
        super().__init__()
        # Support a raw SDK or a wrapper that exposes `.get_sdk()`
        if hasattr(client_or_sdk, "get_sdk"):
            sdk_obj = getattr(client_or_sdk, "get_sdk")()
            self._sdk: Elasticsearch = cast(Elasticsearch, sdk_obj)
        else:
            self._sdk = cast(Elasticsearch, client_or_sdk)

    # ---- helpers ----
    @staticmethod
    def _params(**kwargs: object) -> Dict[str, object]:
        # Filter out Nones to avoid overriding SDK defaults
        out: Dict[str, object] = {}
        for k, v in kwargs.items():
            if v is None:
                continue
            if isinstance(v, (list, dict)) and len(v) == 0:
                continue
            out[k] = v
        return out
    def info(self) -> ElasticsearchResponse:
        """Get cluster info."""
        result = self._sdk.info()
        return ElasticsearchResponse(success=True, data=result)
    def get_cluster_health(self) -> ElasticsearchResponse:
        """Get cluster health status."""
        result = self._sdk.cluster.health()
        return ElasticsearchResponse(success=True, data=result)
    def get_cluster_stats(self) -> ElasticsearchResponse:
        """Get cluster statistics."""
        result = self._sdk.cluster.stats()
        return ElasticsearchResponse(success=True, data=result)
    def list_indices(self) -> ElasticsearchResponse:
        """List all indices and their aliases."""
        result = self._sdk.indices.get_alias(index='*')
        return ElasticsearchResponse(success=True, data=result)
    def create_index(self, index: str, body: Optional[Dict[str, object]] = None) -> ElasticsearchResponse:
        """Create an index with optional settings/mappings."""
        params = self._params(index=index, body=body)
        result = self._sdk.indices.create(**params)
        return ElasticsearchResponse(success=True, data=result)
    def delete_index(self, index: str) -> ElasticsearchResponse:
        """Delete an index."""
        result = self._sdk.indices.delete(index=index)
        return ElasticsearchResponse(success=True, data=result)
    def get_mapping(self, index: str) -> ElasticsearchResponse:
        """Get mapping for an index."""
        result = self._sdk.indices.get_mapping(index=index)
        return ElasticsearchResponse(success=True, data=result)
    def search(self, index: str, body: Optional[Dict[str, object]] = None, size: Optional[int] = None, from_: Optional[int] = None, sort: Optional[str] = None) -> ElasticsearchResponse:
        """Search documents in an index."""
        params = self._params(index=index, body=body, size=size, sort=sort)
        if from_ is not None:
            params['from_'] = from_
        result = self._sdk.search(**params)
        return ElasticsearchResponse(success=True, data=result)
    def index_document(self, index: str, body: Dict[str, object], doc_id: Optional[str] = None) -> ElasticsearchResponse:
        """Index (create/update) a document."""
        params = self._params(index=index, body=body, id=doc_id)
        result = self._sdk.index(**params)
        return ElasticsearchResponse(success=True, data=result)
    def get_document(self, index: str, doc_id: str) -> ElasticsearchResponse:
        """Get a document by ID."""
        result = self._sdk.get(index=index, id=doc_id)
        return ElasticsearchResponse(success=True, data=result)
    def delete_document(self, index: str, doc_id: str) -> ElasticsearchResponse:
        """Delete a document by ID."""
        result = self._sdk.delete(index=index, id=doc_id)
        return ElasticsearchResponse(success=True, data=result)
    def count(self, index: str, body: Optional[Dict[str, object]] = None) -> ElasticsearchResponse:
        """Count documents in an index."""
        params = self._params(index=index, body=body)
        result = self._sdk.count(**params)
        return ElasticsearchResponse(success=True, data=result)
    def bulk(self, body: List[Dict[str, object]], index: Optional[str] = None) -> ElasticsearchResponse:
        """Perform bulk operations."""
        params = self._params(body=body, index=index)
        result = self._sdk.bulk(**params)
        return ElasticsearchResponse(success=True, data=result)
    def scroll(self, scroll_id: str, scroll: str = '5m') -> ElasticsearchResponse:
        """Continue a scroll search."""
        result = self._sdk.scroll(scroll_id=scroll_id, scroll=scroll)
        return ElasticsearchResponse(success=True, data=result)
    def clear_scroll(self, scroll_id: str) -> ElasticsearchResponse:
        """Clear a scroll context."""
        result = self._sdk.clear_scroll(scroll_id=scroll_id)
        return ElasticsearchResponse(success=True, data=result)

