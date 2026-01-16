from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
)


@ConnectorBuilder("Gmail")\
    .in_group("Google Workspace")\
    .with_description("Sync emails and messages from Gmail")\
    .with_categories(["Email"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Gmail",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            redirect_uri="connectors/oauth/callback/Gmail",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[
                    'https://www.googleapis.com/auth/gmail.readonly',
                    "https://www.googleapis.com/auth/documents.readonly",
                    "https://www.googleapis.com/auth/spreadsheets.readonly",
                    "https://www.googleapis.com/auth/presentations.readonly",
                ],
                agent=[]
            ),
            fields=[
                CommonFields.client_id("Google Cloud Console"),
                CommonFields.client_secret("Google Cloud Console")
            ],
            icon_path="/assets/icons/connectors/gmail.svg",
            app_group="Google Workspace",
            app_description="OAuth application for accessing Gmail API and Google Workspace services",
            app_categories=["Email"],
            additional_params={
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true"
            }
        )
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/gmail.svg")
        .with_realtime_support(True)
        .add_documentation_link(DocumentationLink(
            "Gmail API Setup",
            "https://developers.google.com/workspace/guides/auth-overview",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/google-workspace/gmail/gmail',
            'pipeshub'
        ))
        .with_webhook_config(True, ["message.created", "message.modified", "message.deleted"])
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(True)
    )\
    .build_decorator()
class GmailConnector:
    """Gmail connector built with the builder pattern"""

    def __init__(self) -> None:
        self.name = "Gmail"

    def connect(self) -> bool:
        """Connect to Gmail"""
        print(f"Connecting to {self.name}")
        return True


@ConnectorBuilder("Slack")\
    .in_group("Slack")\
    .with_description("Sync messages and channels from Slack")\
    .with_categories(["Messaging"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            AuthField(
                name="userOAuthAccessToken",
                display_name="User OAuth Access Token",
                placeholder="xoxp-...",
                description="The User OAuth Access Token from Slack App settings",
                field_type="PASSWORD",
                max_length=8000,
                is_secret=True
            )
        ])
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/slack.svg")
        .add_documentation_link(DocumentationLink(
            "Slack Bot Token Setup",
            "https://api.slack.com/authentication/basics",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/slack/slack',
            'pipeshub'
        ))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_sync_support(False)
        .with_agent_support(True)
    )\
    .build_decorator()
class SlackConnector:
    """Slack connector built with the builder pattern"""

    def __init__(self) -> None:
        self.name = "Slack"

    def connect(self) -> bool:
        """Connect to Slack"""
        print(f"Connecting to {self.name}")
        return True


@ConnectorBuilder("Notion")\
    .in_group("Notion")\
    .with_description("Sync messages and channels from Notion")\
    .with_categories(["Messaging"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            AuthField(
                name="apiToken",
                display_name="Api Token",
                placeholder="ntn-...",
                description="The Access Token from Notion App settings",
                field_type="PASSWORD",
                max_length=8000,
                is_secret=True
            )
        ])
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/notion.svg")
        .add_documentation_link(DocumentationLink(
            "Notion Bot Token Setup",
            "https://api.notion.com/authentication/basics",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/notion/notion',
            'pipeshub'
        ))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_sync_support(False)
        .with_agent_support(True)
    )\
    .build_decorator()
class  NotionConnector:
    """Notion connector built with the builder pattern"""

    def __init__(self) -> None:
        self.name = "Notion"

    def connect(self) -> bool:
        """Connect to Notion"""
        print(f"Connecting to {self.name}")
        return True



@ConnectorBuilder("Calendar")\
    .in_group("Google Workspace")\
    .with_description("Sync calendar events from Google Calendar")\
    .with_categories(["Calendar"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Calendar",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            redirect_uri="connectors/oauth/callback/Calendar",
            scopes=OAuthScopeConfig(
                personal_sync=[
                    "https://www.googleapis.com/auth/calendar",
                    "https://www.googleapis.com/auth/calendar.events",
                    "https://www.googleapis.com/auth/calendar.readonly"
                ],
                team_sync=[
                    "https://www.googleapis.com/auth/calendar",
                    "https://www.googleapis.com/auth/calendar.events",
                    "https://www.googleapis.com/auth/calendar.readonly"
                ],
                agent=[]
            ),
            fields=[
                CommonFields.client_id("Google Cloud Console"),
                CommonFields.client_secret("Google Cloud Console")
            ],
            icon_path="/assets/icons/connectors/calendar.svg",
            app_group="Google Workspace",
            app_description="OAuth application for accessing Google Calendar API",
            app_categories=["Calendar"],
            additional_params={
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true"
            }
        )
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/calendar.svg")
        .with_realtime_support(True)
        .add_documentation_link(DocumentationLink(
            "Calendar API Setup",
            "https://developers.google.com/workspace/guides/auth-overview",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/google-workspace/calendar/calendar',
            'pipeshub'
        ))
        .with_webhook_config(True, ["event.created", "event.modified", "event.deleted"])
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_sync_support(False)
        .with_agent_support(True)
    )\
    .build_decorator()
class CalendarConnector:
    """Calendar connector built with the builder pattern"""

    def __init__(self) -> None:
        self.name = "Calendar"

    def connect(self) -> bool:
        """Connect to Calendar"""
        print(f"Connecting to {self.name}")
        return True


@ConnectorBuilder("Meet")\
    .in_group("Google Workspace")\
    .with_description("Sync calendar events from Google Meet")\
    .with_categories(["Meet"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Meet",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            redirect_uri="connectors/oauth/callback/Meet",
            scopes=OAuthScopeConfig(
                personal_sync=[
                    "https://www.googleapis.com/auth/meetings.space.created",
                    "https://www.googleapis.com/auth/meetings.space.settings",
                    "https://www.googleapis.com/auth/meetings.space.readonly",
                    "https://www.googleapis.com/auth/calendar",
                    "https://www.googleapis.com/auth/calendar.events"
                ],
                team_sync=[
                    "https://www.googleapis.com/auth/meetings.space.created",
                    "https://www.googleapis.com/auth/meetings.space.settings",
                    "https://www.googleapis.com/auth/meetings.space.readonly",
                    "https://www.googleapis.com/auth/calendar",
                    "https://www.googleapis.com/auth/calendar.events"
                ],
                agent=[]
            ),
            fields=[
                CommonFields.client_id("Google Cloud Console"),
                CommonFields.client_secret("Google Cloud Console")
            ],
            icon_path="/assets/icons/connectors/meet.svg",
            app_group="Google Workspace",
            app_description="OAuth application for accessing Google Meet API and Calendar integration",
            app_categories=["Meet"],
            additional_params={
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true"
            }
        )
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/meet.svg")
        .with_realtime_support(True)
        .add_documentation_link(DocumentationLink(
            "Meet API Setup",
            "https://developers.google.com/workspace/guides/auth-overview",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/google-workspace/meet/meet',
            'pipeshub'
        ))
        .with_webhook_config(True, ["space.created", "space.modified", "space.deleted"])
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_sync_support(False)
        .with_agent_support(True)
    )\
    .build_decorator()
class MeetConnector:
    """Meet connector built with the builder pattern"""

    def __init__(self) -> None:
        self.name = "Meet"

    def connect(self) -> bool:
        """Connect to Meet"""
        print(f"Connecting to {self.name}")
        return True


@ConnectorBuilder("Docs")\
    .in_group("Google Workspace")\
    .with_description("Sync calendar events from Google Docs")\
    .with_categories(["Docs"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Docs",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            redirect_uri="connectors/oauth/callback/Docs",
            scopes=OAuthScopeConfig(
                personal_sync=[
                    "https://www.googleapis.com/auth/documents",
                    "https://www.googleapis.com/auth/documents.readonly",
                    "https://www.googleapis.com/auth/drive",
                    "https://www.googleapis.com/auth/drive.file",
                    "https://www.googleapis.com/auth/drive.readonly"
                ],
                team_sync=[
                    "https://www.googleapis.com/auth/documents",
                    "https://www.googleapis.com/auth/documents.readonly",
                    "https://www.googleapis.com/auth/drive",
                    "https://www.googleapis.com/auth/drive.file",
                    "https://www.googleapis.com/auth/drive.readonly"
                ],
                agent=[]
            ),
            fields=[
                CommonFields.client_id("Google Cloud Console"),
                CommonFields.client_secret("Google Cloud Console")
            ],
            icon_path="/assets/icons/connectors/docs.svg",
            app_group="Google Workspace",
            app_description="OAuth application for accessing Google Docs API and Drive integration",
            app_categories=["Docs"],
            additional_params={
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true"
            }
        )
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/docs.svg")
        .with_realtime_support(True)
        .add_documentation_link(DocumentationLink(
            "Docs API Setup",
            "https://developers.google.com/workspace/guides/auth-overview",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/google-workspace/docs/docs',
            'pipeshub'
        ))
        .with_webhook_config(True, ["document.created", "document.modified", "document.deleted"])
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_sync_support(False)
        .with_agent_support(True)
    )\
    .build_decorator()
class DocsConnector:
    """Docs connector built with the builder pattern"""

    def __init__(self) -> None:
        self.name = "Docs"

    def connect(self) -> bool:
        """Connect to Docs"""
        print(f"Connecting to {self.name}")
        return True


@ConnectorBuilder("Sheets")\
    .in_group("Google Workspace")\
    .with_description("Sync calendar events from Google Sheets")\
    .with_categories(["Sheets"])\
    .with_scopes([ConnectorScope.PERSONAL.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Sheets",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            redirect_uri="connectors/oauth/callback/Sheets",
            scopes=OAuthScopeConfig(
                personal_sync=[
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/spreadsheets.readonly",
                ],
                team_sync=[],
                agent=[]
            ),
            fields=[
                CommonFields.client_id("Google Cloud Console"),
                CommonFields.client_secret("Google Cloud Console")
            ],
            icon_path="/assets/icons/connectors/sheets.svg",
            app_group="Google Workspace",
            app_description="OAuth application for accessing Google Sheets API",
            app_categories=["Sheets"],
            additional_params={
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true"
            }
        )
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/sheets.svg")
        .with_realtime_support(True)
        .add_documentation_link(DocumentationLink(
            "Sheets API Setup",
            "https://developers.google.com/workspace/guides/auth-overview",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/google-workspace/sheets/sheets',
            'pipeshub'
        ))
        .with_webhook_config(True, ["sheet.created", "sheet.modified", "sheet.deleted"])
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_sync_support(False)
        .with_agent_support(True)
    )\
    .build_decorator()
class SheetsConnector:
    """Sheets connector built with the builder pattern"""

    def __init__(self) -> None:
        self.name = "Sheets"

    def connect(self) -> bool:
        """Connect to Sheets"""
        print(f"Connecting to {self.name}")
        return True

@ConnectorBuilder("Forms")\
    .in_group("Google Workspace")\
    .with_description("Sync calendar events from Google Forms")\
    .with_categories(["Forms"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Forms",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            redirect_uri="connectors/oauth/callback/Forms",
            scopes=OAuthScopeConfig(
                personal_sync=[
                    "https://www.googleapis.com/auth/forms.body",
                    "https://www.googleapis.com/auth/forms.body.readonly",
                    "https://www.googleapis.com/auth/forms.responses.readonly",
                    "https://www.googleapis.com/auth/drive",
                    "https://www.googleapis.com/auth/drive.file",
                    "https://www.googleapis.com/auth/drive.readonly"
                ],
                team_sync=[
                    "https://www.googleapis.com/auth/forms.body",
                    "https://www.googleapis.com/auth/forms.body.readonly",
                    "https://www.googleapis.com/auth/forms.responses.readonly",
                    "https://www.googleapis.com/auth/drive",
                    "https://www.googleapis.com/auth/drive.file",
                    "https://www.googleapis.com/auth/drive.readonly"
                ],
                agent=[]
            ),
            fields=[
                CommonFields.client_id("Google Cloud Console"),
                CommonFields.client_secret("Google Cloud Console")
            ],
            icon_path="/assets/icons/connectors/forms.svg",
            app_group="Google Workspace",
            app_description="OAuth application for accessing Google Forms API and Drive integration",
            app_categories=["Forms"],
            additional_params={
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true"
            }
        )
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/forms.svg")
        .with_realtime_support(True)
        .add_documentation_link(DocumentationLink(
            "Forms API Setup",
            "https://developers.google.com/workspace/guides/auth-overview",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/google-workspace/forms/forms',
            'pipeshub'
        ))
        .with_webhook_config(True, ["form.created", "form.modified", "form.deleted"])
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_sync_support(False)
        .with_agent_support(True)
    )\
    .build_decorator()
class FormsConnector:
    """Forms connector built with the builder pattern"""

    def __init__(self) -> None:
        self.name = "Forms"

    def connect(self) -> bool:
        """Connect to Forms"""
        print(f"Connecting to {self.name}")
        return True

@ConnectorBuilder("Slides")\
    .in_group("Google Workspace")\
    .with_description("Sync calendar events from Google Sheets")\
    .with_categories(["Slides"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Slides",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            redirect_uri="connectors/oauth/callback/Slides",
            scopes=OAuthScopeConfig(
                personal_sync=[
                    "https://www.googleapis.com/auth/presentations",
                    "https://www.googleapis.com/auth/presentations.readonly",
                    "https://www.googleapis.com/auth/drive.file",
                    "https://www.googleapis.com/auth/drive.readonly"
                ],
                team_sync=[
                    "https://www.googleapis.com/auth/presentations",
                    "https://www.googleapis.com/auth/presentations.readonly",
                    "https://www.googleapis.com/auth/drive.file",
                    "https://www.googleapis.com/auth/drive.readonly"
                ],
                agent=[]
            ),
            fields=[
                CommonFields.client_id("Google Cloud Console"),
                CommonFields.client_secret("Google Cloud Console")
            ],
            icon_path="/assets/icons/connectors/slides.svg",
            app_group="Google Workspace",
            app_description="OAuth application for accessing Google Slides API and Drive integration",
            app_categories=["Slides"],
            additional_params={
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true"
            }
        )
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/slides.svg")
        .with_realtime_support(True)
        .add_documentation_link(DocumentationLink(
            "Slides API Setup",
            "https://developers.google.com/workspace/guides/auth-overview",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/google-workspace/slides/slides',
            'pipeshub'
        ))
        .with_webhook_config(True, ["slide.created", "slide.modified", "slide.deleted"])
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_sync_support(False)
        .with_agent_support(True)
    )\
    .build_decorator()
class SlidesConnector:
    """Slides connector built with the builder pattern"""

    def __init__(self) -> None:
        self.name = "Slides"

    def connect(self) -> bool:
        """Connect to Slides"""
        print(f"Connecting to {self.name}")
        return True


@ConnectorBuilder("Airtable")\
    .in_group("Airtable")\
    .with_description("Sync messages, tables and views from Airtable")\
    .with_categories(["Database"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            AuthField(
                name="apiToken",
                display_name="Api Token",
                placeholder="atp-...",
                description="The API Access Token from Airtable App settings",
                field_type="PASSWORD",
                max_length=8000,
                is_secret=True
            )
        ])
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/airtable.svg")
        .add_documentation_link(DocumentationLink(
            "Airtable API Token Setup",
            "https://api.airtable.com/authentication/basics",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/airtable/airtable',
            'pipeshub'
        ))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_sync_support(False)
        .with_agent_support(True)
    )\
    .build_decorator()
class AirtableConnector:
    """Airtable connector built with the builder pattern"""

    def __init__(self) -> None:
        self.name = "Airtable"

    def connect(self) -> bool:
        """Connect to Airtable"""
        print(f"Connecting to {self.name}")
        return True


@ConnectorBuilder("Azure Blob")\
    .in_group("Azure")\
    .with_description("Sync files and folders from Azure Blob Storage")\
    .with_categories(["Storage"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.ACCOUNT_KEY).fields([
            AuthField(
                name="accountName",
                display_name="Account Name",
                placeholder="mystorageaccount",
                description="The Account Name from Azure Blob Storage App settings",
                field_type="TEXT",
                max_length=2000
            ),
            AuthField(
                name="accountKey",
                display_name="Account Key",
                placeholder="Your account key",
                description="The Account Key from Azure Blob Storage App settings",
                field_type="PASSWORD",
                max_length=2000,
                is_secret=True
            ),
            AuthField(
                name="containerName",
                display_name="Container Name",
                placeholder="my-container",
                description="The Container Name from Azure Blob Storage App settings",
                field_type="TEXT",
                max_length=2000
            ),
            AuthField(
                name="endpointProtocol",
                display_name="Endpoint Protocol",
                placeholder="https",
                description="The Endpoint Protocol from Azure Blob Storage App settings",
                field_type="TEXT",
                max_length=2000
            ),
            AuthField(
                name="endpointSuffix",
                display_name="Endpoint Suffix",
                placeholder="core.windows.net",
                description="The Endpoint Suffix from Azure Blob Storage App settings",
                field_type="TEXT",
                max_length=2000
            )
        ])
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/azureblob.svg")
        .add_documentation_link(DocumentationLink(
            "Azure Blob Storage Connection String Setup",
            "https://learn.microsoft.com/en-us/azure/storage/blobs/storage-quickstart-blobs-portal",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/azure/azureblob',
            'pipeshub'
        ))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_sync_support(False)
        .with_agent_support(True)
    )\
    .build_decorator()
class AzureBlobConnector:
    """Azure Blob connector built with the builder pattern"""

    def __init__(self) -> None:
        self.name = "Azure Blob"

    def connect(self) -> bool:
        """Connect to Azure Blob"""
        print(f"Connecting to {self.name}")
        return True


@ConnectorBuilder("Linear")\
    .in_group("Linear")\
    .with_description("Sync issues and projects from Linear")\
    .with_categories(["Issue Tracking"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            AuthField(
                name="apiToken",
                display_name="API Token",
                placeholder="Enter your API Token",
                description="The API Token from Linear instance (https://linear.app/settings/api)",
                field_type="PASSWORD",
                max_length=2000,
                is_secret=True
            )
        ])
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/linear.svg")
        .add_documentation_link(DocumentationLink(
            "Linear API Token Setup",
            "https://linear.app/developers/docs/authentication",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/linear/linear',
            'pipeshub'
        ))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_sync_support(False)
        .with_agent_support(True)
    )\
    .build_decorator()
class LinearConnector:
    """Linear connector built with the builder pattern"""

    def __init__(self) -> None:
        self.name = "Linear"

    def connect(self) -> bool:
        """Connect to Linear"""
        print(f"Connecting to {self.name}")
        return True

@ConnectorBuilder("Zendesk")\
    .in_group("Zendesk")\
    .with_description("Sync tickets and users from Zendesk")\
    .with_categories(["Issue Tracking"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            AuthField(
                name="apiToken",
                display_name="API Token",
                placeholder="Enter your API Token",
                description="The API Token from Zendesk instance",
                field_type="PASSWORD",
                max_length=2000,
                is_secret=True
            ),
            AuthField(
                name="email",
                display_name="Email",
                placeholder="Enter your Email",
                description="The Email from Zendesk instance",
                field_type="TEXT",
                max_length=2000
            ),
            AuthField(
                name="subdomain",
                display_name="Subdomain",
                placeholder="Enter your Subdomain",
                description="The Subdomain from Zendesk instance",
                field_type="TEXT",
                max_length=2000
            )
        ])
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/zendesk.svg")
        .add_documentation_link(DocumentationLink(
            "Zendesk API Token Setup",
            "https://developer.zendesk.com/documentation/ticketing/introduction/authentication/",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/zendesk/zendesk',
            'pipeshub'
        ))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_sync_support(False)
        .with_agent_support(True)
    )\
    .build_decorator()
class ZendeskConnector:
    """Zendesk connector built with the builder pattern"""

    def __init__(self) -> None:
        self.name = "Zendesk"

    def connect(self) -> bool:
        """Connect to Zendesk"""
        print(f"Connecting to {self.name}")
        return True
