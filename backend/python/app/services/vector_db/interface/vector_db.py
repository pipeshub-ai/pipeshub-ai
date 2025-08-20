from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Union

from qdrant_client.http.models import Filter  # type: ignore

from app.services.vector_db.qdrant.filter import QdrantFilterMode


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
        vectors_config: dict,
        sparse_vectors_config: dict,
        optimizers_config: dict,
        quantization_config: dict,
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
        index_type: str = "persistent",
    ) -> None:
        raise NotImplementedError("create_index() is not implemented")

    @abstractmethod
    async def filter_collection(
        self,
        filter_mode: Union[str, QdrantFilterMode] = QdrantFilterMode.MUST,
        must: Optional[Dict[str, Any]] = None,
        should: Optional[Dict[str, Any]] = None,
        must_not: Optional[Dict[str, Any]] = None,
        min_should_match: Optional[int] = None,
        **filters: Any,
    ) -> Filter:
        raise NotImplementedError("filter_collection() is not implemented")

    @abstractmethod
    async def scroll(self, collection_name: str, scroll_filter: Filter, limit: int) -> object:
        raise NotImplementedError("scroll() is not implemented")
