from abc import ABC, abstractmethod


class IClient(ABC):
    @abstractmethod
    def get_client(self) -> object:
        raise NotImplementedError("Not implemented")
