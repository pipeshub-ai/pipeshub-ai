from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App, AppGroup


class ServicenowKBApp(App):
    """ServiceNow Knowledge Base App definition"""

    def __init__(self) -> None:
        super().__init__(Connectors.SERVICENOWKB, AppGroups.SERVICENOW)


class ServiceNowAppGroup(AppGroup):
    """ServiceNow App Group containing all ServiceNow connectors"""

    def __init__(self) -> None:
        super().__init__(AppGroups.SERVICENOW, [ServicenowKBApp()])
