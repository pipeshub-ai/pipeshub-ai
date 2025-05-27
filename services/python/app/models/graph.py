from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseModel(ABC):
    """Base model for all database entities"""

    @abstractmethod
    def to_dict(self) -> Dict[str, Any]:
        """Convert model to dictionary representation"""
        pass

    @abstractmethod
    def validate(self) -> bool:
        """Validate the model against schema"""
        pass

    @property
    @abstractmethod
    def key(self) -> str:
        """Get the unique key for this model"""
        pass

class Node(BaseModel):
    """Base class for all graph nodes"""
    pass

class Edge(BaseModel):
    """Base class for all graph edges"""

    @property
    @abstractmethod
    def from_node(self) -> str:
        """Get the source node reference"""
        pass

    @property
    @abstractmethod
    def to_node(self) -> str:
        """Get the target node reference"""
        pass
