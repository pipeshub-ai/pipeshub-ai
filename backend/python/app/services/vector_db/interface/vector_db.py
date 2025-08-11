from abc import ABC, abstractmethod
from typing import TypeVar

Client = TypeVar("Client")

class IVectorDBService(ABC):
    @abstractmethod
    async def connect(self) -> None:
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        pass

    @abstractmethod
    async def get_service_name(self) -> str:
        pass

    @abstractmethod
    async def get_service_client(self) -> Client:
        pass
