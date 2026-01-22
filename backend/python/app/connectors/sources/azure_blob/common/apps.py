"""Azure Blob Storage App definition."""

from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App


class AzureBlobApp(App):
    """App definition for Azure Blob Storage connector."""

    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.AZURE_BLOB, AppGroups.AZURE, connector_id)
