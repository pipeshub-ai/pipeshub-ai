from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.connectors.enums.enums import AuthenticationType, ConnectorType


@dataclass
class ConnectorConfig:
    """Configuration for a connector"""

    connector_type: ConnectorType
    authentication_type: AuthenticationType
    base_url: str
    api_version: str
    rate_limits: dict[str, int]
    scopes: list[str]
    webhook_support: bool
    batch_operations: bool
    real_time_sync: bool


class IConnectorConfig(ABC):
    """Interface for connector configuration management"""

    @abstractmethod
    def get_config(self, connector_type: ConnectorType) -> ConnectorConfig:
        """Get configuration for a specific connector type"""

    @abstractmethod
    def validate_config(self, config: ConnectorConfig) -> bool:
        """Validate connector configuration"""

    @abstractmethod
    def update_config(
        self, connector_type: ConnectorType, config: ConnectorConfig
    ) -> bool:
        """Update connector configuration"""
