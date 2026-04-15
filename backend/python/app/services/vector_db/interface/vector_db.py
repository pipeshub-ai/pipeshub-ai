from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Union

from app.services.vector_db.models import (
    CollectionConfig,
    FilterExpression,
    FilterMode,
    FilterValue,
    HybridSearchRequest,
    SearchResult,
    VectorPoint,
)


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
        config: Optional[CollectionConfig] = None,
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
        filter_mode: Union[str, FilterMode] = FilterMode.MUST,
        must: Optional[Dict[str, FilterValue]] = None,
        should: Optional[Dict[str, FilterValue]] = None,
        must_not: Optional[Dict[str, FilterValue]] = None,
        min_should_match: Optional[int] = None,
        **filters: FilterValue,
    ) -> FilterExpression:
        raise NotImplementedError("filter_collection() is not implemented")

    @abstractmethod
    async def scroll(
        self,
        collection_name: str,
        scroll_filter: FilterExpression,
        limit: int,
    ) -> object:
        raise NotImplementedError("scroll() is not implemented")

    @abstractmethod
    def query_nearest_points(
        self,
        collection_name: str,
        requests: List[HybridSearchRequest],
    ) -> List[List[SearchResult]]:
        raise NotImplementedError("query_nearest_points() is not implemented")

    @abstractmethod
    def upsert_points(
        self,
        collection_name: str,
        points: List[VectorPoint],
    ) -> None:
        raise NotImplementedError("upsert_points() is not implemented")

    @abstractmethod
    def delete_points(
        self,
        collection_name: str,
        filter: FilterExpression,
    ) -> None:
        raise NotImplementedError("delete_points() is not implemented")

    @abstractmethod
    def overwrite_payload(
        self,
        collection_name: str,
        payload: dict,
        points: FilterExpression,
    ) -> None:
        raise NotImplementedError("overwrite_payload() is not implemented")
