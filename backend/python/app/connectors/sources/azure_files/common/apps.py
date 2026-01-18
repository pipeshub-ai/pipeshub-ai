"""Azure Files App definition."""

from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App


class AzureFilesApp(App):
    """App definition for Azure Files connector."""

    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.AZURE_FILES, AppGroups.AZURE, connector_id)
