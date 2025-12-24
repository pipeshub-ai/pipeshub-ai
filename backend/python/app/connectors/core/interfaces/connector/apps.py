from typing import List

from app.config.constants.arangodb import AppGroups, Connectors


class App:
    def __init__(self, app_name: Connectors, app_group_name: AppGroups, connector_id: str) -> None:
        self.app_name = app_name
        self.app_group_name = app_group_name
        self.connector_id = connector_id

    def get_app_name(self) -> Connectors:
        return self.app_name

    def get_app_group_name(self) -> AppGroups:
        return self.app_group_name

    def get_connector_id(self) -> str:
        return self.connector_id

class AppGroup:
    def __init__(self, app_group_name: AppGroups, apps: List[App], connector_id: str) -> None:
        self.app_group_name = app_group_name
        self.apps = apps
        self.connector_id = connector_id

    def get_app_group_name(self) -> AppGroups:
        return self.app_group_name

    def get_connector_id(self) -> str:
        return self.connector_id
