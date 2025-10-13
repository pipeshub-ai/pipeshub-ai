from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from app.models.graph import Edge, Node


class IGraphService(ABC):
    """Interface for graph database operations"""
    @abstractmethod
    async def connect(self) -> bool:
        pass

    @abstractmethod
    async def disconnect(self) -> bool:
        pass

    @abstractmethod
    async def get_service_name(self) -> str:
        pass

    @abstractmethod
    async def get_service_client(self) -> object:
        pass

    @abstractmethod
    async def create_graph(self, graph_name: str) -> bool:
        pass

    @abstractmethod
    async def create_node(self, node_type: str, node_id: str) -> bool:
        pass

    @abstractmethod
    async def create_edge(self, edge_type: str, from_node: str, to_node: str) -> bool:
        pass

    @abstractmethod
    async def delete_graph(self) -> bool:
        pass

    @abstractmethod
    async def delete_node(self, node_type: str, node_id: str) -> bool:
        pass

    @abstractmethod
    async def delete_edge(self, edge_type: str, from_node: str, to_node: str) -> bool:
        pass

    @abstractmethod
    async def get_node(self, node_type: str, node_id: str) -> Node | None:
        pass

    @abstractmethod
    async def get_edge(self, edge_type: str, from_node: str, to_node: str) -> Edge | None:
        pass

    @abstractmethod
    async def get_nodes(self, node_type: str) -> List[Node]:
        pass

    @abstractmethod
    async def get_edges(self, edge_type: str) -> List[Edge]:
        pass

    # Additional methods for document operations
    @abstractmethod
    async def create_collection(self, collection_name: str) -> bool:
        """Create a new collection"""
        pass

    @abstractmethod
    async def upsert_document(self, collection_name: str, document: Dict[str, Any]) -> bool:
        """Insert or update a document in a collection"""
        pass

    @abstractmethod
    async def get_document(self, collection_name: str, document_key: str) -> Optional[Dict[str, Any]]:
        """Get a document by key from a collection"""
        pass

    @abstractmethod
    async def delete_document(self, collection_name: str, document_key: str) -> bool:
        """Delete a document by key from a collection"""
        pass

    @abstractmethod
    async def execute_query(self, query: str, bind_vars: Optional[Dict[str, Any]] = None) -> Optional[List[Dict[str, Any]]]:
        """Execute an AQL query. Returns None on error, empty list if successful but no results."""
        pass

    @abstractmethod
    async def create_index(self, collection_name: str, fields: List[str], index_type: str = "persistent") -> bool:
        """Create an index on a collection"""
        pass
