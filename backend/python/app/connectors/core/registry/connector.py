from app.connectors.core.registry.connector_registry import Connector


@Connector(
    name="DRIVE",
    app_group="Google Workspace",
    auth_type="OAUTH",
    supports_realtime=True,
    config={
        "auth": {
            "type": "OAUTH",
            "displayRedirectUri": True,
            "redirectUri": "https://your-app.com/auth/callback/google",
            "documentationLinks": [
                {
                    "title": "Google Drive API Setup",
                    "url": "https://developers.google.com/drive/api/quickstart",
                    "type": "setup"
                }
            ],
            "schema": {
                "fields": [
                    {
                        "name": "clientId",
                        "displayName": "Client ID",
                        "placeholder": "Enter your Google Client ID",
                        "fieldType": "TEXT",
                        "required": True
                    },
                    {
                        "name": "clientSecret",
                        "displayName": "Client Secret",
                        "placeholder": "Enter your Google Client Secret",
                        "fieldType": "PASSWORD",
                        "required": True
                    }
                ]
            },
            "values": {},
            "customFields": [],
            "customValues": {}
        },
        "sync": {
            "supportedStrategies": ["WEBHOOK", "SCHEDULED", "MANUAL"],
            "webhookConfig": {
                "supported": True,
                "events": ["file.created", "file.modified", "file.deleted"]
            },
            "scheduledConfig": {
                "enabled": False,
                "intervalMinutes": 30
            },
            "customFields": [
                {
                    "name": "batchSize",
                    "displayName": "Batch Size",
                    "fieldType": "SELECT",
                    "options": ["25", "50", "100"],
                    "defaultValue": "50"
                }
            ],
            "customValues": {}
        },
        "filters": {
            "schema": {
                "fields": [
                    {
                        "name": "fileTypes",
                        "displayName": "File Types",
                        "fieldType": "MULTISELECT",
                        "options": ["document", "spreadsheet", "presentation", "pdf"]
                    }
                ]
            },
            "values": {},
            "customFields": [],
            "customValues": {}
        }
    }
)
class GoogleDriveConnector:
    """Google Drive connector with full config schema"""

    def __init__(self) -> None:
        self.name = "DRIVE"

    def connect(self) -> bool:
        """Connect to Google Drive"""
        print(f"Connecting to {self.name}")
        return True


@Connector(
    name="GMAIL",
    app_group="Google Workspace",
    auth_type="OAUTH",
    supports_realtime=True,
    config={
        "auth": {
            "type": "OAUTH",
            "displayRedirectUri": True,
            "schema": {
                "fields": [
                    {
                        "name": "clientId",
                        "displayName": "Client ID",
                        "fieldType": "TEXT",
                        "required": True
                    },
                    {
                        "name": "clientSecret",
                        "displayName": "Client Secret",
                        "fieldType": "PASSWORD",
                        "required": True
                    }
                ]
            },
            "values": {},
            "customFields": [],
            "customValues": {}
        },
        "sync": {
            "supportedStrategies": ["WEBHOOK", "SCHEDULED"],
            "customFields": [],
            "customValues": {}
        },
        "filters": {
            "schema": {
                "fields": [
                    {
                        "name": "labels",
                        "displayName": "Gmail Labels",
                        "fieldType": "MULTISELECT"
                    }
                ]
            },
            "values": {},
            "customFields": [],
            "customValues": {}
        }
    }
)
class GmailConnector:
    """Gmail connector with full config schema"""

    def __init__(self) -> None:
        self.name = "GMAIL"

    def connect(self) -> bool:
        """Connect to Gmail"""
        print(f"Connecting to {self.name}")
        return True


@Connector(
    name="ONEDRIVE",
    app_group="Microsoft 365",
    auth_type="OAUTH_ADMIN_CONSENT",
    supports_realtime=False,
    config={
        "auth": {
            "type": "OAUTH_ADMIN_CONSENT",
            "displayRedirectUri": False,
            "schema": {
                "fields": [
                    {
                        "name": "clientId",
                        "displayName": "Application ID",
                        "fieldType": "TEXT",
                        "required": True
                    },
                    {
                        "name": "clientSecret",
                        "displayName": "Client Secret",
                        "fieldType": "PASSWORD",
                        "required": True
                    },
                    {
                        "name": "tenantId",
                        "displayName": "Tenant ID",
                        "fieldType": "TEXT",
                        "required": True
                    }
                ]
            },
            "values": {},
            "customFields": [],
            "customValues": {}
        },
        "sync": {
            "supportedStrategies": ["SCHEDULED"],
            "customFields": [],
            "customValues": {}
        },
        "filters": {
            "schema": {
                "fields": [
                    {
                        "name": "fileTypes",
                        "displayName": "File Types",
                        "fieldType": "MULTISELECT"
                    }
                ]
            },
            "values": {},
            "customFields": [],
            "customValues": {}
        }
    }
)
class OneDriveConnector:
    """OneDrive connector with full config schema"""

    def __init__(self) -> None:
        self.name = "ONEDRIVE"

    def connect(self) -> bool:
        """Connect to OneDrive"""
        print(f"Connecting to {self.name}")
        return True


@Connector(
    name="SLACK",
    app_group="Slack",
    auth_type="API_TOKEN",
    supports_realtime=False,
    config={
        "auth": {
            "type": "API_TOKEN",
            "displayRedirectUri": False,
            "schema": {
                "fields": [
                    {
                        "name": "botToken",
                        "displayName": "Bot Token",
                        "placeholder": "xoxb-...",
                        "fieldType": "PASSWORD",
                        "required": True
                    }
                ]
            },
            "values": {},
            "customFields": [],
            "customValues": {}
        },
        "sync": {
            "supportedStrategies": ["SCHEDULED", "MANUAL"],
            "customFields": [],
            "customValues": {}
        },
        "filters": {
            "schema": {
                "fields": [
                    {
                        "name": "channels",
                        "displayName": "Channels",
                        "fieldType": "MULTISELECT"
                    }
                ]
            },
            "values": {},
            "customFields": [],
            "customValues": {}
        }
    }
)
class SlackConnector:
    """Slack connector with full config schema"""

    def __init__(self) -> None:
        self.name = "SLACK"

    def connect(self) -> bool:
        """Connect to Slack"""
        print(f"Connecting to {self.name}")
        return True
