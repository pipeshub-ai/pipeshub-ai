from abc import ABC, abstractmethod
from typing import TypeVar

ClientType = TypeVar('ClientType')

class IGraphService(ABC):
    """Interface for graph database operations"""
    @abstractmethod
    async def connect(self) -> bool | None:
        pass

    @abstractmethod
    async def disconnect(self) -> bool | None:
        pass

    @abstractmethod
    async def get_service_name(self) -> str:
        pass

    @abstractmethod
    async def get_service_client(self) -> ClientType:
        pass

    @abstractmethod
    async def create_graph(self, graph_name: str) -> None:
        pass

    @abstractmethod
    async def create_node(self, node_type: str, node_id: str) -> None:
        pass

    @abstractmethod
    async def create_edge(self, edge_type: str, from_node: str, to_node: str) -> None:
        pass

    @abstractmethod
    async def delete_graph(self) -> None:
        pass

    @abstractmethod
    async def delete_node(self, node_type: str, node_id: str) -> None:
        pass

    @abstractmethod
    async def delete_edge(self, edge_type: str, from_node: str, to_node: str) -> None:
        pass

    @abstractmethod
    async def get_node(self, node_type: str, node_id: str) -> None:
        pass

    @abstractmethod
    async def get_edge(self, edge_type: str, from_node: str, to_node: str) -> None:
        pass

    @abstractmethod
    async def get_nodes(self, node_type: str) -> None:
        pass

    @abstractmethod
    async def get_edges(self, edge_type: str) -> None:
        pass
