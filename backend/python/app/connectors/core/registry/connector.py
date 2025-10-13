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
            "https://developers.google.com/workspace/guides/auth-overview"
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


# @ConnectorBuilder("SharePoint Online")\
#     .in_group("Microsoft 365")\
#     .with_auth_type("OAUTH_ADMIN_CONSENT")\
#     .with_description("Sync documents and lists from SharePoint Online")\
#     .with_categories(["Storage", "Documentation"])\
#     .configure(lambda builder: builder
#         .with_icon("/assets/icons/connectors/sharepoint.svg")
#         .add_documentation_link(DocumentationLink(
#             "SharePoint Online API Setup",
#             "https://docs.microsoft.com/en-us/sharepoint/dev/sp-add-ins/register-sharepoint-add-ins"
#         ))
#         .with_redirect_uri("http://localhost:3001/sharepoint/oauth/callback", False)
#         .add_auth_field(AuthField(
#             name="clientId",
#             display_name="Application (Client) ID",
#             placeholder="Enter your Azure AD Application ID",
#             description="The Application (Client) ID from Azure AD App Registration"
#         ))
#         .add_auth_field(AuthField(
#             name="clientSecret",
#             display_name="Client Secret",
#             placeholder="Enter your Azure AD Client Secret",
#             description="The Client Secret from Azure AD App Registration",
#             field_type="PASSWORD",
#             is_secret=True
#         ))
#         .add_auth_field(AuthField(
#             name="tenantId",
#             display_name="Directory (Tenant) ID (Optional)",
#             placeholder="Enter your Azure AD Tenant ID",
#             description="The Directory (Tenant) ID from Azure AD"
#         ))
#         .add_auth_field(AuthField(
#             name="hasAdminConsent",
#             display_name="Has Admin Consent",
#             description="Check if admin consent has been granted for the application",
#             field_type="CHECKBOX",
#             required=True,
#             default_value=False
#         ))
#         .add_auth_field(AuthField(
#             name="sharepointDomain",
#             display_name="SharePoint Domain",
#             placeholder="https://your-domain.sharepoint.com",
#             description="Your SharePoint domain URL",
#             field_type="URL",
#             max_length=2000
#         ))
#         .add_auth_field(AuthField(
#             name="redirectUri",
#             display_name="Redirect URI",
#             placeholder="http://localhost:3001/sharepoint/oauth/callback",
#             description="The redirect URI for OAuth authentication",
#             field_type="URL",
#             required=False,
#             max_length=2000
#         ))
#         .add_conditional_display("redirectUri", "hasAdminConsent", "equals", False)
#         .with_sync_strategies(["SCHEDULED", "MANUAL"])
#         .with_scheduled_config(True, 60)
#         .add_filter_field(FilterField(
#             name="sites",
#             display_name="SharePoint Sites",
#             description="Select SharePoint sites to sync content from"
#         ), "https://graph.microsoft.com/v1.0/sites")
#         .add_filter_field(FilterField(
#             name="documentLibraries",
#             display_name="Document Libraries",
#             description="Select document libraries to sync from"
#         ), "https://graph.microsoft.com/v1.0/sites/{siteId}/drives")
#         .add_filter_field(CommonFields.file_types_filter(), "static")
#     )\
#     .build_decorator()
# class SharePointConnector:
#     """SharePoint connector built with the builder pattern"""

#     def __init__(self) -> None:
#         self.name = "SharePoint Online"

#     def connect(self) -> bool:
#         """Connect to SharePoint"""
#         print(f"Connecting to {self.name}")
#         return True


# @ConnectorBuilder("OneDrive")\
#     .in_group("Microsoft 365")\
#     .with_auth_type("OAUTH_ADMIN_CONSENT")\
#     .with_description("Sync files and folders from OneDrive")\
#     .with_categories(["Storage"])\
#     .configure(lambda builder: builder
#         .with_icon("/assets/icons/connectors/onedrive.svg")
#         .add_documentation_link(DocumentationLink(
#             "Azure AD App Registration Setup",
#             "https://docs.microsoft.com/en-us/azure/active-directory/develop/quickstart-register-app"
#         ))
#         .with_redirect_uri("http://localhost:3001/onedrive/oauth/callback", False)
#         .add_auth_field(AuthField(
#             name="clientId",
#             display_name="Application (Client) ID",
#             placeholder="Enter your Azure AD Application ID",
#             description="The Application (Client) ID from Azure AD App Registration"
#         ))
#         .add_auth_field(AuthField(
#             name="clientSecret",
#             display_name="Client Secret",
#             placeholder="Enter your Azure AD Client Secret",
#             description="The Client Secret from Azure AD App Registration",
#             field_type="PASSWORD",
#             is_secret=True
#         ))
#         .add_auth_field(AuthField(
#             name="tenantId",
#             display_name="Directory (Tenant) ID",
#             placeholder="Enter your Azure AD Tenant ID",
#             description="The Directory (Tenant) ID from Azure AD"
#         ))
#         .add_auth_field(AuthField(
#             name="hasAdminConsent",
#             display_name="Has Admin Consent",
#             description="Check if admin consent has been granted for the application",
#             field_type="CHECKBOX",
#             required=True,
#             default_value=False
#         ))
#         .add_auth_field(AuthField(
#             name="redirectUri",
#             display_name="Redirect URI",
#             placeholder="http://localhost:3001/onedrive/oauth/callback",
#             description="The redirect URI for OAuth authentication",
#             field_type="URL",
#             required=False,
#             max_length=2000
#         ))
#         .add_conditional_display("redirectUri", "hasAdminConsent", "equals", False)
#         .with_sync_strategies(["SCHEDULED", "MANUAL"])
#         .with_scheduled_config(True, 60)
#         .add_filter_field(CommonFields.file_types_filter(), "static")
#         .add_filter_field(CommonFields.folders_filter(),
#                           "https://graph.microsoft.com/v1.0/me/drive/root/children")
#     )\
#     .build_decorator()
# class OneDriveConnector:
#     """OneDrive connector built with the builder pattern"""

#     def __init__(self) -> None:
#         self.name = "OneDrive"

#     def connect(self) -> bool:
#         """Connect to OneDrive"""
#         print(f"Connecting to {self.name}")
#         return True


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
            "https://developers.google.com/workspace/guides/auth-overview"
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
            "https://api.slack.com/authentication/basics"
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
            "https://api.notion.com/authentication/basics"
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
            "https://developers.google.com/workspace/guides/auth-overview"
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
            "https://developers.google.com/workspace/guides/auth-overview"
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
            "https://developers.google.com/workspace/guides/auth-overview"
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
            "https://developers.google.com/workspace/guides/auth-overview"
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
            "https://developers.google.com/workspace/guides/auth-overview"
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
            "https://developers.google.com/workspace/guides/auth-overview"
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
