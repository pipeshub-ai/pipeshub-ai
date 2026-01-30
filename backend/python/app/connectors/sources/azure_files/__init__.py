"""Azure Files Connector package."""

from app.connectors.sources.azure_files.connector import (
    AzureFilesConnector,
    AzureFilesDataSourceEntitiesProcessor,
)

__all__ = [
    "AzureFilesConnector",
    "AzureFilesDataSourceEntitiesProcessor",
]
