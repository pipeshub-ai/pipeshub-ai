from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Union

from qdrant_client.http.models import (  # type: ignore
    Filter,
    PointStruct,
    QueryRequest,
)

from app.services.vector_db.qdrant.filter import QdrantFilterMode

# Type alias for filter values
FilterValue = Union[str, int, float, bool, List[Union[str, int, float, bool]]]


class IVectorDBService(ABC):
    @abstractmethod
    async def connect(self) -> None:
        raise NotImplementedError("connect() is not implemented")

    @abstractmethod
    async def disconnect(self) -> None:
        raise NotImplementedError("disconnect() is not implemented")

    @abstractmethod
    def get_service_name(self) -> str:
        raise NotImplementedError("get_service_name() is not implemented")

    @abstractmethod
    def get_service(self) -> 'IVectorDBService':
        raise NotImplementedError("get_service() is not implemented")

    @abstractmethod
    def get_service_client(self) -> object:
        raise NotImplementedError("get_service_client() is not implemented")

    @abstractmethod
    async def create_collection(
        self,
        collection_name: str,
        embedding_size: int=1024,
        sparse_idf: bool = False,
        vectors_config: Optional[dict] = None,
        sparse_vectors_config: Optional[dict] = None,
        optimizers_config: Optional[dict] = None,
        quantization_config: Optional[dict] = None,
    ) -> None:
        raise NotImplementedError("create_collection() is not implemented")

    @abstractmethod
    async def get_collections(self) -> object:
        raise NotImplementedError("get_collections() is not implemented")

    @abstractmethod
    async def get_collection(self, collection_name: str) -> object:
        raise NotImplementedError("get_collection() is not implemented")

    @abstractmethod
    async def delete_collection(self, collection_name: str) -> None:
        raise NotImplementedError("delete_collection() is not implemented")

    @abstractmethod
    async def create_index(
        self,
        collection_name: str,
        field_name: str,
        field_schema: dict,
    ) -> None:
        raise NotImplementedError("create_index() is not implemented")

    @abstractmethod
    async def filter_collection(
        self,
        filter_mode: Union[str, QdrantFilterMode] = QdrantFilterMode.MUST,
        must: Optional[Dict[str, FilterValue]] = None,
        should: Optional[Dict[str, FilterValue]] = None,
        must_not: Optional[Dict[str, FilterValue]] = None,
        min_should_match: Optional[int] = None,
        **filters: FilterValue,
    ) -> Filter:
        raise NotImplementedError("filter_collection() is not implemented")

    @abstractmethod
    async def scroll(self, collection_name: str, scroll_filter: Filter, limit: int) -> object:
        raise NotImplementedError("scroll() is not implemented")

    @abstractmethod
    def query_nearest_points(
        self,
        collection_name: str,
        requests: List[QueryRequest],
    ) -> List[List[PointStruct]]:
        """Query batch points"""
        raise NotImplementedError("query_nearest_points() is not implemented")

    @abstractmethod
    def upsert_points(
        self,
        collection_name: str,
        points: List[PointStruct],
    ) -> None:
        """Upsert points"""
        raise NotImplementedError("upsert() is not implemented")
