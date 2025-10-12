# app/sources/client/iclient.py
from abc import ABC, abstractmethod

class IClient(ABC):
    @abstractmethod
    def get_client(self):
        pass
