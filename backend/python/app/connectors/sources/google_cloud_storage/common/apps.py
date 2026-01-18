"""Google Cloud Storage App definition."""

from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App


class GCSApp(App):
    """App definition for Google Cloud Storage connector."""

    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.GCS, AppGroups.GCS, connector_id)
