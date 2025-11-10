from abc import ABC, abstractmethod
from typing import Any


class BaseModel(ABC):
    """Base model for all database entities"""

    @abstractmethod
    def to_dict(self) -> dict[str, Any]:
        """Convert model to dictionary representation"""

    @abstractmethod
    def validate(self) -> bool:
        """Validate the model against schema"""

    @property
    @abstractmethod
    def key(self) -> str:
        """Get the unique key for this model"""


class Node(BaseModel):
    """Base class for all graph nodes"""


class Edge(BaseModel):
    """Base class for all graph edges"""

    @property
    @abstractmethod
    def from_node(self) -> str:
        """Get the source node reference"""

    @property
    @abstractmethod
    def to_node(self) -> str:
        """Get the target node reference"""
