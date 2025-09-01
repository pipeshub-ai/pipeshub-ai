from typing import Callable, Type


def Connector(
    name: str,
    app_group: str,
    auth_type: str,
    supports_realtime: bool = False
) -> Callable[[Type], Type]:
    """
    Decorator to register a connector with metadata.

    Args:
        name: Name of the application (e.g., "Drive", "Gmail")
        app_group: Group the app belongs to (e.g., "Google Workspace")
        auth_type: Authentication type (e.g., "OAuth", "API Key")
        supports_realtime: Whether connector supports real-time updates
    """
    def decorator(cls) -> Type:
        # Store metadata in the class
        cls._connector_metadata = {
            "name": name,
            "appGroup": app_group,
            "authType": auth_type,
            "supportsRealtime": supports_realtime
        }

        # Mark class as a connector
        cls._is_connector = True

        return cls
    return decorator


@Connector(
    name="DRIVE",
    app_group="Google Workspace",
    auth_type="OAuth",
    supports_realtime=True
)
class GoogleDriveConnector:
    """Example Google Drive connector class"""

    def __init__(self) -> None:
        self.name = "Google Drive"

    def connect(self) -> bool:
        """Connect to Google Drive"""
        print(f"Connecting to {self.name}")
        return True


@Connector(
    name="GMAIL",
    app_group="Google Workspace",
    auth_type="OAuth",
    supports_realtime=True
)
class GmailConnector:
    """Example Gmail connector class"""

    def __init__(self) -> None:
        self.name = "Gmail"

    def connect(self) -> bool:
        """Connect to Gmail"""
        print(f"Connecting to {self.name}")
        return True


@Connector(
    name="ONEDRIVE",
    app_group="Microsoft 365",
    auth_type="OAuth",
    supports_realtime=False
)
class OneDriveConnector:
    """Example OneDrive connector class"""

    def __init__(self) -> None:
        self.name = "OneDrive"

    def connect(self) -> bool:
        """Connect to OneDrive"""
        print(f"Connecting to {self.name}")
        return True

@Connector(
    name="SLACK",
    app_group="Slack",
    auth_type="OAuth",
    supports_realtime=False
)
class SlackConnector:
    """Example Slack connector class"""

    def __init__(self) -> None:
        self.name = "Slack"

    def connect(self) -> bool:
        """Connect to Slack"""
        print(f"Connecting to {self.name}")
        return True
