from abc import ABC, abstractmethod

class IVectorDBService(ABC):
    @abstractmethod
    async def connect(self):
        pass

    @abstractmethod
    async def disconnect(self):
        pass
    
    @abstractmethod
    async def get_service_name(self):
        pass

    @abstractmethod
    async def get_service_client(self):
        pass
