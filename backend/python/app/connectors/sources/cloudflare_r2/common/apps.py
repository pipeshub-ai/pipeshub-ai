from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App


class CloudflareR2App(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.CLOUDFLARE_R2, AppGroups.CLOUDFLARE_R2, connector_id)
