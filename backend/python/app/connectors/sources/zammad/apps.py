"""Zammad App Definition"""
from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App


class ZammadApp(App):
    """Zammad application definition"""
    def __init__(self):
        super().__init__(Connectors.ZAMMAD, AppGroups.ZAMMAD)

