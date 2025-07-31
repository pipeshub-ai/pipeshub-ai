from abc import ABC, abstractmethod
from typing import Dict

class IKeyValueService(ABC):
    @abstractmethod
    async def connect(self):
        pass

    @abstractmethod
    async def disconnect(self):
        pass
    
    @abstractmethod
    async def set(self, key: str, value: str, expire: int = 86400):
        pass

    @abstractmethod
    async def get(self, key: str):
        pass
    
    @abstractmethod
    async def delete(self, key: str):
        pass
    
    @abstractmethod
    async def store_progress(self, progress: Dict):
        pass
    
    @abstractmethod
    async def get_progress(self):
        pass