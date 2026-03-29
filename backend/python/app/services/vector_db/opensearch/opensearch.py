import time
from typing import Dict, List, Optional, Union

from opensearchpy import AsyncOpenSearch, OpenSearch, helpers  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.services.vector_db.const.const import VECTOR_DB_COLLECTION_NAME
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.models import (
    CollectionConfig,
    DistanceMetric,
    FilterExpression,
    FilterMode,
    FilterValue,
    HybridSearchRequest,
    SearchResult,
    VectorPoint,
)
from app.services.vector_db.opensearch.config import OpenSearchConfig
from app.services.vector_db.opensearch.utils import OpenSearchUtils
from app.utils.logger import create_logger

logger = create_logger("opensearch_service")

_SPACE_TYPE_MAP = {
    DistanceMetric.COSINE: "cosinesimil",
    DistanceMetric.L2: "l2",
    DistanceMetric.DOT_PRODUCT: "innerproduct",
}


class OpenSearchService(IVectorDBService):
    def __init__(
        self,
        config_service: ConfigurationService | OpenSearchConfig,
        is_async: bool = False,
    ) -> None:
        self.config_service = config_service
        self.client: Optional[OpenSearch | AsyncOpenSearch] = None
        self.is_async = is_async

    @classmethod
    async def create(
        cls,
        config: ConfigurationService | OpenSearchConfig,
        is_async: bool = False,
    ) -> 'OpenSearchService':
        service = cls(config, is_async=is_async)
        await service.connect()
        return service

    async def connect(self) -> None:
        try:
            if isinstance(self.config_service, ConfigurationService):
                os_config = await self.config_service.get_config(config_node_constants.OPENSEARCH.value)
            else:
                os_config = self.config_service.opensearch_config
            if not os_config:
                raise ValueError("OpenSearch configuration not found")

            host = os_config.get("host", "localhost")  # type: ignore
            port = os_config.get("port", 9200)  # type: ignore
            username = os_config.get("username", "admin")  # type: ignore
            password = os_config.get("password", "admin")  # type: ignore
            use_ssl = os_config.get("useSsl", False)  # type: ignore
            verify_certs = os_config.get("verifyCerts", False)  # type: ignore
            timeout = os_config.get("timeout", 300)  # type: ignore

            client_kwargs = {
                "hosts": [{"host": host, "port": port}],
                "http_auth": (username, password),
                "use_ssl": use_ssl,
                "verify_certs": verify_certs,
                "ssl_show_warn": False,
                "timeout": timeout,
            }

            if self.is_async:
                self.client = AsyncOpenSearch(**client_kwargs)
            else:
                self.client = OpenSearch(**client_kwargs)

            logger.info("Connected to OpenSearch successfully")
        except Exception as e:
            logger.error(f"Failed to connect to OpenSearch: {e}")
            raise

    async def disconnect(self) -> None:
        if self.client is not None:
            try:
                if isinstance(self.client, AsyncOpenSearch):
                    await self.client.close()
                else:
                    self.client.close()
                logger.info("Disconnected from OpenSearch successfully")
            except Exception as e:
                logger.warning(f"Error during disconnect (likely harmless): {e}")
            finally:
                self.client = None

    def get_service_name(self) -> str:
        return "opensearch"

    def get_service(self) -> 'OpenSearchService':
        return self

    def get_service_client(self) -> object:
        return self.client

    async def get_collections(self) -> object:
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")
        return self.client.indices.get_alias(index="*")

    async def get_collection(self, collection_name: str) -> object:
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")
        return self.client.indices.get(index=collection_name)

    async def delete_collection(self, collection_name: str) -> None:
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")
        if self.client.indices.exists(index=collection_name):
            self.client.indices.delete(index=collection_name)
            logger.info(f"Deleted index {collection_name}")

        pipeline_name = f"{collection_name}-search-pipeline"
        try:
            self.client.transport.perform_request("DELETE", f"/_search/pipeline/{pipeline_name}")
        except Exception:
            pass

    async def create_collection(
        self,
        collection_name: str = VECTOR_DB_COLLECTION_NAME,
        config: Optional[CollectionConfig] = None,
    ) -> None:
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")

        if config is None:
            config = CollectionConfig()

        space_type = _SPACE_TYPE_MAP.get(config.distance_metric, "cosinesimil")

        index_body = {
            "settings": {
                "index.knn": True,
                "number_of_shards": 1,
                "number_of_replicas": 0,
            },
            "mappings": {
                "properties": {
                    "dense_embedding": {
                        "type": "knn_vector",
                        "dimension": config.embedding_size,
                        "method": {
                            "engine": "lucene",
                            "space_type": space_type,
                            "name": "hnsw",
                            "parameters": {
                                "ef_construction": 256,
                                "m": 48,
                            },
                        },
                    },
                    "page_content": {
                        "type": "text",
                    },
                    "metadata": {
                        "type": "object",
                        "enabled": True,
                    },
                }
            },
        }

        self.client.indices.create(index=collection_name, body=index_body)
        logger.info(f"Created index {collection_name}")

        # Create search pipeline for hybrid search normalization
        pipeline_name = f"{collection_name}-search-pipeline"
        pipeline_body = {
            "description": "Hybrid search normalization pipeline",
            "phase_results_processors": [
                {
                    "normalization-processor": {
                        "normalization": {
                            "technique": "min_max",
                        },
                        "combination": {
                            "technique": "arithmetic_mean",
                            "parameters": {
                                "weights": [0.3, 0.7],
                            },
                        },
                    }
                }
            ],
        }
        try:
            self.client.transport.perform_request(
                "PUT",
                f"/_search/pipeline/{pipeline_name}",
                body=pipeline_body,
            )
            logger.info(f"Created search pipeline {pipeline_name}")
        except Exception as e:
            logger.warning(f"Failed to create search pipeline: {e}")

    async def create_index(
        self,
        collection_name: str,
        field_name: str,
        field_schema: dict,
    ) -> None:
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")

        os_type = "keyword" if field_schema.get("type") == "keyword" else "text"
        mapping_body = {
            "properties": {
                field_name: {"type": os_type}
            }
        }
        self.client.indices.put_mapping(index=collection_name, body=mapping_body)

    async def filter_collection(
        self,
        filter_mode: Union[str, FilterMode] = FilterMode.MUST,
        must: Optional[Dict[str, FilterValue]] = None,
        should: Optional[Dict[str, FilterValue]] = None,
        must_not: Optional[Dict[str, FilterValue]] = None,
        min_should_match: Optional[int] = None,
        **kwargs: FilterValue,
    ) -> FilterExpression:
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")

        if isinstance(filter_mode, str):
            try:
                filter_mode = FilterMode(filter_mode.lower())
            except ValueError:
                raise ValueError(f"Invalid mode '{filter_mode}'. Must be 'must', 'should', or 'must_not'")

        all_must_filters = dict(must) if must else {}
        all_should_filters = dict(should) if should else {}
        all_must_not_filters = dict(must_not) if must_not else {}

        if kwargs:
            if filter_mode == FilterMode.MUST:
                all_must_filters.update(kwargs)
            elif filter_mode == FilterMode.SHOULD:
                all_should_filters.update(kwargs)
            elif filter_mode == FilterMode.MUST_NOT:
                all_must_not_filters.update(kwargs)

        must_conditions = OpenSearchUtils.build_conditions(all_must_filters) if all_must_filters else []
        should_conditions = OpenSearchUtils.build_conditions(all_should_filters) if all_should_filters else []
        must_not_conditions = OpenSearchUtils.build_conditions(all_must_not_filters) if all_must_not_filters else []

        if not must_conditions and not should_conditions and not must_not_conditions:
            logger.warning("No filters provided - returning empty filter")
            return FilterExpression()

        return FilterExpression(
            must=must_conditions,
            should=should_conditions,
            must_not=must_not_conditions,
            min_should_match=min_should_match if should_conditions else None,
        )

    async def scroll(
        self,
        collection_name: str,
        scroll_filter: FilterExpression,
        limit: int,
    ) -> object:
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")

        bool_query = OpenSearchUtils.filter_expression_to_bool_query(scroll_filter)

        body = {
            "query": bool_query,
            "size": min(limit, 10000),
            "_source": {"exclude": ["dense_embedding"]},
            "sort": [{"_id": "asc"}],
        }

        all_hits = []
        search_after = None

        while len(all_hits) < limit:
            if search_after:
                body["search_after"] = search_after

            result = self.client.search(index=collection_name, body=body)
            hits = result.get("hits", {}).get("hits", [])

            if not hits:
                break

            all_hits.extend(hits)
            search_after = hits[-1].get("sort")

        # Return in a format compatible with Qdrant's scroll (tuple of points, next_offset)
        points = []
        for hit in all_hits[:limit]:
            source = hit.get("_source", {})
            point = type('Point', (), {
                'id': hit.get("_id"),
                'payload': {
                    "metadata": source.get("metadata", {}),
                    "page_content": source.get("page_content", ""),
                },
            })()
            points.append(point)

        return (points, None)

    def query_nearest_points(
        self,
        collection_name: str,
        requests: List[HybridSearchRequest],
    ) -> List[List[SearchResult]]:
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")

        pipeline_name = f"{collection_name}-search-pipeline"
        all_results: List[List[SearchResult]] = []

        for req in requests:
            filter_query = {}
            if req.filter is not None:
                filter_query = OpenSearchUtils.filter_expression_to_bool_query(req.filter)

            body = OpenSearchUtils.build_hybrid_query(req, filter_query)

            try:
                result = self.client.search(
                    index=collection_name,
                    body=body,
                    params={"search_pipeline": pipeline_name},
                )
            except Exception:
                # Fallback: try without search pipeline (e.g., if pipeline doesn't exist)
                result = self.client.search(index=collection_name, body=body)

            hits = result.get("hits", {}).get("hits", [])
            batch_results = [OpenSearchUtils.hit_to_search_result(hit) for hit in hits]
            all_results.append(batch_results)

        return all_results

    def upsert_points(
        self,
        collection_name: str,
        points: List[VectorPoint],
        batch_size: int = 500,
        max_workers: int = 1,
    ) -> None:
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")

        start_time = time.perf_counter()
        total_points = len(points)
        logger.info(f"Starting upsert of {total_points} points to index '{collection_name}'")

        actions = []
        for point in points:
            doc = OpenSearchUtils.vector_point_to_document(point)
            actions.append({
                "_index": collection_name,
                "_id": point.id,
                "_source": doc,
            })

        if actions:
            success, errors = helpers.bulk(
                self.client,
                actions,
                chunk_size=batch_size,
                raise_on_error=True,
            )
            if errors:
                logger.error(f"Bulk upsert had {len(errors)} errors")
                raise RuntimeError(f"Bulk upsert failed with {len(errors)} errors")

        elapsed_time = time.perf_counter() - start_time
        throughput = total_points / elapsed_time if elapsed_time > 0 else 0
        logger.info(
            f"Completed upsert of {total_points} points in {elapsed_time:.2f}s "
            f"(throughput: {throughput:.1f} points/s)"
        )

    def delete_points(
        self,
        collection_name: str,
        filter: FilterExpression,
    ) -> None:
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")

        bool_query = OpenSearchUtils.filter_expression_to_bool_query(filter)
        self.client.delete_by_query(
            index=collection_name,
            body={"query": bool_query},
        )
        logger.info(f"Deleted points from index '{collection_name}'")

    def overwrite_payload(
        self,
        collection_name: str,
        payload: dict,
        points: FilterExpression,
    ) -> None:
        if self.client is None:
            raise RuntimeError("Client not connected. Call connect() first.")

        bool_query = OpenSearchUtils.filter_expression_to_bool_query(points)

        # Build painless script to update fields
        script_parts = []
        params = {}
        for key, value in payload.items():
            param_name = f"p_{key}"
            script_parts.append(f"ctx._source['{key}'] = params.{param_name}")
            params[param_name] = value

        script_source = "; ".join(script_parts)

        self.client.update_by_query(
            index=collection_name,
            body={
                "query": bool_query,
                "script": {
                    "source": script_source,
                    "params": params,
                },
            },
        )
