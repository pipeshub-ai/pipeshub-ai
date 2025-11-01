from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    DocumentationLink,
    FilterField,
)


@ConnectorBuilder("Drive")\
    .in_group("Google Workspace")\
    .with_auth_type("OAUTH")\
    .with_description("Sync files and folders from Google Drive")\
    .with_categories(["Storage"])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/drive.svg")
        .with_realtime_support(True)
        .add_documentation_link(DocumentationLink(
            "Google Drive API Setup",
            "https://developers.google.com/workspace/guides/auth-overview",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/google-workspace/drive/drive',
            'pipeshub'
        ))
        .with_redirect_uri("connectors/oauth/callback/Drive", True)
        .with_oauth_urls(
            "https://accounts.google.com/o/oauth2/v2/auth",
            "https://oauth2.googleapis.com/token",
            ["https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/drive.metadata.readonly",
            "https://www.googleapis.com/auth/drive.metadata",
            "https://www.googleapis.com/auth/documents.readonly",
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/presentations.readonly",
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/drive",
            ]
        )
        .add_auth_field(CommonFields.client_id("Google Cloud Console"))
        .add_auth_field(CommonFields.client_secret("Google Cloud Console"))
        .with_webhook_config(True, ["file.created", "file.modified", "file.deleted"])
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .add_sync_custom_field(CommonFields.batch_size_field())
        .add_filter_field(CommonFields.file_types_filter(), "static")
        .add_filter_field(CommonFields.folders_filter(),
                          "https://www.googleapis.com/drive/v3/files?q=mimeType='application/vnd.google-apps.folder'&fields=files(id,name,parents)")
    )\
    .build_decorator()
class GoogleDriveConnector:
    """Google Drive connector built with the builder pattern"""

    def __init__(self) -> None:
        self.name = "Drive"

    def connect(self) -> bool:
        """Connect to Google Drive"""
        print(f"Connecting to {self.name}")
        return True


@ConnectorBuilder("Gmail")\
    .in_group("Google Workspace")\
    .with_auth_type("OAUTH")\
    .with_description("Sync emails and messages from Gmail")\
    .with_categories(["Email"])\
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
        .with_redirect_uri("connectors/oauth/callback/Gmail", True)
        .with_oauth_urls(
            "https://accounts.google.com/o/oauth2/v2/auth",
            "https://oauth2.googleapis.com/token",
            [
                'https://www.googleapis.com/auth/gmail.readonly',
                "https://www.googleapis.com/auth/documents.readonly",
                "https://www.googleapis.com/auth/spreadsheets.readonly",
                "https://www.googleapis.com/auth/presentations.readonly",
            ]
        )
        .add_auth_field(CommonFields.client_id("Google Cloud Console"))
        .add_auth_field(CommonFields.client_secret("Google Cloud Console"))
        .with_webhook_config(True, ["message.created", "message.modified", "message.deleted"])
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .add_filter_field(FilterField(
            name="labels",
            display_name="Gmail Labels",
            description="Select Gmail labels to sync messages from"
        ), "https://gmail.googleapis.com/gmail/v1/users/me/labels")
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
    .with_auth_type("API_TOKEN")\
    .with_description("Sync messages and channels from Slack")\
    .with_categories(["Messaging"])\
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
        .with_redirect_uri("", False)
        .add_auth_field(AuthField(
            name="botToken",
            display_name="Bot Token",
            placeholder="xoxb-...",
            description="The Bot User OAuth Access Token from Slack App settings",
            field_type="PASSWORD",
            max_length=8000,
            is_secret=True
        ))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .add_filter_field(CommonFields.channels_filter(),
                          "https://slack.com/api/conversations.list")
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
    .with_auth_type("API_TOKEN")\
    .with_description("Sync messages and channels from Notion")\
    .with_categories(["Messaging"])\
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
        .with_redirect_uri("", False)
        .add_auth_field(AuthField(
            name="apiToken",
            display_name="Api Token",
            placeholder="ntn-...",
            description="The Access Token from Notion App settings",
            field_type="PASSWORD",
            max_length=8000,
            is_secret=True
        ))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
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
    .with_auth_type("OAUTH")\
    .with_description("Sync calendar events from Google Calendar")\
    .with_categories(["Calendar"])\
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
        .with_redirect_uri("connectors/oauth/callback/Calendar", True)
        .with_oauth_urls(
            "https://accounts.google.com/o/oauth2/v2/auth",
            "https://oauth2.googleapis.com/token",
            [
                "https://www.googleapis.com/auth/calendar",  # Full calendar access (read/write)
                "https://www.googleapis.com/auth/calendar.events",  # Events read/write
                "https://www.googleapis.com/auth/calendar.readonly"  # Read-only access
            ]
        )
        .add_auth_field(CommonFields.client_id("Google Cloud Console"))
        .add_auth_field(CommonFields.client_secret("Google Cloud Console"))
        .with_webhook_config(True, ["event.created", "event.modified", "event.deleted"])
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
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
    .with_auth_type("OAUTH")\
    .with_description("Sync calendar events from Google Meet")\
    .with_categories(["Meet"])\
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
        .with_redirect_uri("connectors/oauth/callback/Meet", True)
        .with_oauth_urls(
            "https://accounts.google.com/o/oauth2/v2/auth",
            "https://oauth2.googleapis.com/token",
            [
                "https://www.googleapis.com/auth/meetings.space.created",
                "https://www.googleapis.com/auth/meetings.space.settings",
                "https://www.googleapis.com/auth/meetings.space.readonly",
                "https://www.googleapis.com/auth/calendar",  # Often needed for Meet integration
                "https://www.googleapis.com/auth/calendar.events"
            ]
        )
        .add_auth_field(CommonFields.client_id("Google Cloud Console"))
        .add_auth_field(CommonFields.client_secret("Google Cloud Console"))
        .with_webhook_config(True, ["space.created", "space.modified", "space.deleted"])
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
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
    .with_auth_type("OAUTH")\
    .with_description("Sync calendar events from Google Docs")\
    .with_categories(["Docs"])\
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
        .with_redirect_uri("connectors/oauth/callback/Docs", True)
        .with_oauth_urls(
            "https://accounts.google.com/o/oauth2/v2/auth",
            "https://oauth2.googleapis.com/token",
            [
                "https://www.googleapis.com/auth/documents",          # Full docs access
                "https://www.googleapis.com/auth/documents.readonly", # Read-only docs
                "https://www.googleapis.com/auth/drive",
                "https://www.googleapis.com/auth/drive.file",
                "https://www.googleapis.com/auth/drive.readonly"      # Drive readonly
            ]
        )
        .add_auth_field(CommonFields.client_id("Google Cloud Console"))
        .add_auth_field(CommonFields.client_secret("Google Cloud Console"))
        .with_webhook_config(True, ["document.created", "document.modified", "document.deleted"])
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
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
    .with_auth_type("OAUTH")\
    .with_description("Sync calendar events from Google Sheets")\
    .with_categories(["Sheets"])\
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
        .with_redirect_uri("connectors/oauth/callback/Sheets", True)
        .with_oauth_urls(
            "https://accounts.google.com/o/oauth2/v2/auth",
            "https://oauth2.googleapis.com/token",
            [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            ]
        )
        .add_auth_field(CommonFields.client_id("Google Cloud Console"))
        .add_auth_field(CommonFields.client_secret("Google Cloud Console"))
        .with_webhook_config(True, ["sheet.created", "sheet.modified", "sheet.deleted"])
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
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
    .with_auth_type("OAUTH")\
    .with_description("Sync calendar events from Google Forms")\
    .with_categories(["Forms"])\
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
        .with_redirect_uri("connectors/oauth/callback/Forms", True)
        .with_oauth_urls(
            "https://accounts.google.com/o/oauth2/v2/auth",
            "https://oauth2.googleapis.com/token",
            [
                "https://www.googleapis.com/auth/forms.body",         # Full forms access
                "https://www.googleapis.com/auth/forms.body.readonly", # Forms readonly
                "https://www.googleapis.com/auth/forms.responses.readonly", # Responses readonly
                "https://www.googleapis.com/auth/drive",
                "https://www.googleapis.com/auth/drive.file",
                "https://www.googleapis.com/auth/drive.readonly"
            ]
        )
        .add_auth_field(CommonFields.client_id("Google Cloud Console"))
        .add_auth_field(CommonFields.client_secret("Google Cloud Console"))
        .with_webhook_config(True, ["form.created", "form.modified", "form.deleted"])
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
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
    .with_auth_type("OAUTH")\
    .with_description("Sync calendar events from Google Sheets")\
    .with_categories(["Slides"])\
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
        .with_redirect_uri("connectors/oauth/callback/Slides", True)
        .with_oauth_urls(
            "https://accounts.google.com/o/oauth2/v2/auth",
            "https://oauth2.googleapis.com/token",
            [
                "https://www.googleapis.com/auth/presentations",          # Full presentations access
                "https://www.googleapis.com/auth/presentations.readonly", # Read-only
                "https://www.googleapis.com/auth/drive.file",            # For file access
                "https://www.googleapis.com/auth/drive.readonly"          # Drive readonly
            ]
        )
        .add_auth_field(CommonFields.client_id("Google Cloud Console"))
        .add_auth_field(CommonFields.client_secret("Google Cloud Console"))
        .with_webhook_config(True, ["slide.created", "slide.modified", "slide.deleted"])
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
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
    .with_auth_type("API_TOKEN")\
    .with_description("Sync messages, tables and views from Airtable")\
    .with_categories(["Database"])\
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
        .with_redirect_uri("", False)
        .add_auth_field(AuthField(
            name="apiToken",
            display_name="Api Token",
            placeholder="atp-...",
            description="The API Access Token from Airtable App settings",
            field_type="PASSWORD",
            max_length=8000,
            is_secret=True
        ))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
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
    .with_auth_type("ACCOUNT_KEY")\
    .with_description("Sync files and folders from Azure Blob Storage")\
    .with_categories(["Storage"])\
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
        .with_redirect_uri("", False)
        .add_auth_field(AuthField(
            name="accountName",
            display_name="Account Name",
            placeholder="mystorageaccount",
            description="The Account Name from Azure Blob Storage App settings",
            field_type="TEXT",
            max_length=2000
        ))
        .add_auth_field(AuthField(
            name="accountKey",
            display_name="Account Key",
            placeholder="Your account key",
            description="The Account Key from Azure Blob Storage App settings",
            field_type="PASSWORD",
            max_length=2000,
            is_secret=True
        ))
        .add_auth_field(AuthField(
            name="containerName",
            display_name="Container Name",
            placeholder="my-container",
            description="The Container Name from Azure Blob Storage App settings",
            field_type="TEXT",
            max_length=2000
        ))
        .add_auth_field(AuthField(
            name="endpointProtocol",
            display_name="Endpoint Protocol",
            placeholder="https",
            description="The Endpoint Protocol from Azure Blob Storage App settings",
            field_type="TEXT",
            max_length=2000
        ))
        .add_auth_field(AuthField(
            name="endpointSuffix",
            display_name="Endpoint Suffix",
            placeholder="core.windows.net",
            description="The Endpoint Suffix from Azure Blob Storage App settings",
            field_type="TEXT",
            max_length=2000
        ))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
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


@ConnectorBuilder("BookStack")\
    .in_group("BookStack")\
    .with_auth_type("BEARER_TOKEN")\
    .with_description("Sync books and pages from BookStack")\
    .with_categories(["Documentation"])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/bookstack.svg")
        .add_documentation_link(DocumentationLink(
            "BookStack API Token Setup",
            "https://bookstack.org/docs/admin/authentication/",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/bookstack/bookstack',
            'pipeshub'
        ))
        .with_redirect_uri("", False)
        .add_auth_field(AuthField(
            name="tokenId",
            display_name="Token ID",
            placeholder="Enter your Token ID",
            description="The Token ID from BookStack instance",
            field_type="TEXT",
            max_length=2000
        ))
        .add_auth_field(AuthField(
            name="tokenSecret",
            display_name="Token Secret",
            placeholder="Enter your Token Secret",
            description="The Token Secret from BookStack instance",
            field_type="PASSWORD",
            max_length=2000,
            is_secret=True
        ))
        .add_auth_field(AuthField(
            name="baseURL",
            display_name="Base URL",
            placeholder="https://bookstack.example.com",
            description="The Base URL from BookStack instance",
            field_type="TEXT",
            max_length=2000
        ))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
    )\
    .build_decorator()
class BookStackConnector:
    """BookStack connector built with the builder pattern"""

    def __init__(self) -> None:
        self.name = "BookStack"

    def connect(self) -> bool:
        """Connect to BookStack"""
        print(f"Connecting to {self.name}")
        return True


@ConnectorBuilder("Linear")\
    .in_group("Linear")\
    .with_auth_type("API_TOKEN")\
    .with_description("Sync issues and projects from Linear")\
    .with_categories(["Issue Tracking"])\
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
        .with_redirect_uri("", False)
        .add_auth_field(AuthField(
            name="apiToken",
            display_name="API Token",
            placeholder="Enter your API Token",
            description="The API Token from Linear instance (https://linear.app/settings/api)",
            field_type="PASSWORD",
            max_length=2000,
            is_secret=True
        ))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
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


@ConnectorBuilder("S3")\
    .in_group("S3")\
    .with_auth_type("ACCESS_KEY")\
    .with_description("Sync files and folders from S3")\
    .with_categories(["Storage"])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/s3.svg")
        .add_documentation_link(DocumentationLink(
            "S3 Access Key Setup",
            "https://docs.aws.amazon.com/general/latest/gr/aws-sec-cred-types.html#access-keys-and-secret-access-keys",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/s3/s3',
            'pipeshub'
        ))
        .with_redirect_uri("", False)
        .add_auth_field(AuthField(
            name="accessKey",
            display_name="Access Key",
            placeholder="Enter your Access Key",
            description="The Access Key from S3 instance",
            field_type="PASSWORD",
            max_length=2000,
            is_secret=True
        ))
        .add_auth_field(AuthField(
            name="secretKey",
            display_name="Secret Key",
            placeholder="Enter your Secret Key",
            description="The Secret Key from S3 instance",
            field_type="PASSWORD",
            max_length=2000,
            is_secret=True
        ))
        .add_auth_field(AuthField(
            name="region",
            display_name="Region",
            placeholder="Enter your Region Name",
            description="The Region from S3 instance",
            field_type="TEXT",
            max_length=2000
        ))
        .add_auth_field(AuthField(
            name="bucket",
            display_name="Bucket Name",
            placeholder="Enter your Bucket Name",
            description="The Bucket from S3 instance",
            field_type="TEXT",
            max_length=2000
        ))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
    )\
    .build_decorator()
class S3Connector:
    """S3 connector built with the builder pattern"""

    def __init__(self) -> None:
        self.name = "S3"

    def connect(self) -> bool:
        """Connect to S3"""
        print(f"Connecting to {self.name}")
        return True


@ConnectorBuilder("ServiceNow")\
    .in_group("ServiceNow")\
    .with_auth_type("USERNAME_PASSWORD")\
    .with_description("Sync issues and projects from ServiceNow")\
    .with_categories(["Issue Tracking"])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/servicenow.svg")
        .add_documentation_link(DocumentationLink(
            "ServiceNow Username Password Setup",
            "https://docs.servicenow.com/bundle/rome-it-service-management/page/product/integration/reference/r_ITSMIntegrationAPI.html",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/servicenow/servicenow',
            'pipeshub'
        ))
        .with_redirect_uri("", False)
        .add_auth_field(AuthField(
            name="username",
            display_name="Username",
            placeholder="Enter your Username",
            description="The Username from ServiceNow instance",
            field_type="TEXT",
            max_length=2000
        ))
        .add_auth_field(AuthField(
            name="password",
            display_name="Password",
            placeholder="Enter your Password",
            description="The Password from ServiceNow instance",
            field_type="PASSWORD",
            max_length=2000,
            is_secret=True
        ))
        .add_auth_field(AuthField(
            name="instanceUrl",
            display_name="Instance URL",
            placeholder="Enter your Instance URL",
            description="The Instance URL from ServiceNow instance",
            field_type="TEXT",
            max_length=2000
        ))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
    )\
    .build_decorator()
class ServiceNowConnector:
    """ServiceNow connector built with the builder pattern"""

    def __init__(self) -> None:
        self.name = "ServiceNow"

    def connect(self) -> bool:
        """Connect to ServiceNow"""
        print(f"Connecting to {self.name}")
        return True

@ConnectorBuilder("Zendesk")\
    .in_group("Zendesk")\
    .with_auth_type("API_TOKEN")\
    .with_description("Sync tickets and users from Zendesk")\
    .with_categories(["Issue Tracking"])\
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
        .with_redirect_uri("", False)
        .add_auth_field(AuthField(
            name="apiToken",
            display_name="API Token",
            placeholder="Enter your API Token",
            description="The API Token from Zendesk instance",
            field_type="PASSWORD",
            max_length=2000,
            is_secret=True
        ))
        .add_auth_field(AuthField(
            name="email",
            display_name="Email",
            placeholder="Enter your Email",
            description="The Email from Zendesk instance",
            field_type="TEXT",
            max_length=2000
        ))
        .add_auth_field(AuthField(
            name="subdomain",
            display_name="Subdomain",
            placeholder="Enter your Subdomain",
            description="The Subdomain from Zendesk instance",
            field_type="TEXT",
            max_length=2000
        ))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
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
