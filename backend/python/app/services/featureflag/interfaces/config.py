from abc import ABC, abstractmethod


class IConfigProvider(ABC):
    """Interface for configuration providers (Open/Closed Principle - open for extension)
    Future providers: EtcdProvider, HeaderOverrideProvider, DatabaseProvider
    """

    @abstractmethod
    def get_flag_value(self, flag_name: str) -> bool | None:
        """Get the boolean value of a feature flag"""

    @abstractmethod
    def refresh(self) -> None:
        """Refresh configuration from source"""
