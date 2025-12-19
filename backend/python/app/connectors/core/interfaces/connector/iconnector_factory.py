from abc import ABC, abstractmethod

from app.connectors.core.base.connector.connector_service import (
    BaseConnector,
)
from app.connectors.core.interfaces.connector.iconnector_config import ConnectorConfig
from app.connectors.enums.enums import ConnectorType


class IConnectorFactory(ABC):
    """Base interface for connector factories"""

    @abstractmethod
    def create_connector(self, connector_type: ConnectorType, config: ConnectorConfig) -> BaseConnector:
        """Create a connector instance"""

    @abstractmethod
    def get_supported_connectors(self) -> list[ConnectorType]:
        """Get list of supported connector types"""

    @abstractmethod
    def validate_connector_type(self, connector_type: ConnectorType) -> bool:
        """Validate if connector type is supported"""

