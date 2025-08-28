from typing import List, Optional


class App:
    def __init__(self, app_name: str, app_group_name: Optional[str] = None) -> None:
        self.app_name = app_name
        self.app_group_name = app_group_name

    def get_app_name(self) -> str:
        return self.app_name

    def get_app_group_name(self) -> str:
        return self.app_group_name

class AppGroup:
    def __init__(self, app_group_name: str, apps: List[App]) -> None:
        self.app_group_name = app_group_name
        self.apps = apps

    def get_app_group_name(self) -> str:
        return self.app_group_name
