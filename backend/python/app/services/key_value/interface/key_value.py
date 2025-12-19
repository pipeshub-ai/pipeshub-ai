from abc import ABC, abstractmethod


class IKeyValueService(ABC):
    @abstractmethod
    async def connect(self) -> bool:
        pass

    @abstractmethod
    async def disconnect(self) -> bool:
        pass

    @abstractmethod
    async def set(self, key: str, value: str, expire: int = 86400) -> bool:
        pass

    @abstractmethod
    async def get(self, key: str) -> str | None:
        pass

    @abstractmethod
    async def delete(self, key: str) -> bool:
        pass

    @abstractmethod
    async def store_progress(self, progress: dict) -> bool:
        pass

    @abstractmethod
    async def get_progress(self) -> dict | None:
        pass
