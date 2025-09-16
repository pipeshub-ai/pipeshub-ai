from typing import List

from app.config.constants.arangodb import AppGroups, Connectors


class App:
    def __init__(self, app_name: Connectors, app_group_name: AppGroups) -> None:
        self.app_name = app_name
        self.app_group_name = app_group_name

    def get_app_name(self) -> Connectors:
        return self.app_name

    def get_app_group_name(self) -> AppGroups:
        return self.app_group_name

class AppGroup:
    def __init__(self, app_group_name: AppGroups, apps: List[App]) -> None:
        self.app_group_name = app_group_name
        self.apps = apps

    def get_app_group_name(self) -> AppGroups:
        return self.app_group_name
