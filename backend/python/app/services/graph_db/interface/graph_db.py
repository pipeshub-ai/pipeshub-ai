from abc import ABC, abstractmethod
from typing import List

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
