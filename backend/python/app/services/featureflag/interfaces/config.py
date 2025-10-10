from abc import ABC, abstractmethod
from typing import Optional


class IConfigProvider(ABC):
    """
    Interface for configuration providers (Open/Closed Principle - open for extension)
    Future providers: EtcdProvider, HeaderOverrideProvider, DatabaseProvider
    """

    @abstractmethod
    def get_flag_value(self, flag_name: str) -> Optional[bool]:
        """Get the boolean value of a feature flag"""
        pass

    @abstractmethod
    def refresh(self) -> None:
        """Refresh configuration from source"""
        pass
