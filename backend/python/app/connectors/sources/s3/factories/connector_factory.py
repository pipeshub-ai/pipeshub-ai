"""Factory for creating S3 connector instances"""

import logging

from app.connectors.core.factory.connector_factory import UniversalConnectorFactory
from app.connectors.core.interfaces.connector.iconnector_service import (
    IConnectorService,
)
from app.connectors.enums.enums import ConnectorType
from app.connectors.sources.s3.config.config import S3_CONFIG
from app.connectors.sources.s3.services.authentication_service import (
    S3AuthenticationService,
)
from app.connectors.sources.s3.services.connector_service import S3ConnectorService
from app.connectors.sources.s3.services.data_service import S3DataService


class S3ConnectorFactory:
    """Factory for creating S3 connector instances"""

    @staticmethod
    def register_with_factory(factory: UniversalConnectorFactory) -> None:
        """Register S3 connector with the universal factory"""
        factory.register_connector_implementation(
            connector_type=ConnectorType.S3,
            connector_class=S3ConnectorService,
            auth_service_class=S3AuthenticationService,
            data_service_class=S3DataService,
            config=S3_CONFIG
        )

    @staticmethod
    def create_connector(logger: logging.Logger) -> IConnectorService:
        """Create an S3 connector instance"""
        factory = UniversalConnectorFactory(logger)
        S3ConnectorFactory.register_with_factory(factory)
        return factory.create_connector(ConnectorType.S3, S3_CONFIG)
