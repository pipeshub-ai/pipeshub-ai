from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App, AppGroup


class ServicenowApp(App):
    """ServiceNow Knowledge Base App definition"""

    def __init__(self) -> None:
        super().__init__(Connectors.SERVICENOW, AppGroups.SERVICENOW)


class ServiceNowAppGroup(AppGroup):
    """ServiceNow App Group containing all ServiceNow connectors"""

    def __init__(self) -> None:
        super().__init__(AppGroups.SERVICENOW, [ServicenowApp()])
