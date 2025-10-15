from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App


class BookStackApp(App):
    def __init__(self) -> None:
        super().__init__(Connectors.BOOKSTACK.value, AppGroups.BOOKSTACK.value)
