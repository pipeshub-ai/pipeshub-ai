GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/calendar.readonly"
]

GOOGLE_CONNECTOR_ENTERPRISE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
    "https://www.googleapis.com/auth/admin.directory.group.readonly",
    "https://www.googleapis.com/auth/admin.directory.domain.readonly",
    "https://www.googleapis.com/auth/admin.reports.audit.readonly",
    "https://www.googleapis.com/auth/admin.directory.orgunit",
]


GOOGLE_CONNECTOR_ENTERPRISE_SCOPES_FULL = [
    # Gmail - Full email management
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    "https://www.googleapis.com/auth/gmail.settings.sharing",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/gmail.metadata",

    # Drive - Full file management
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
    "https://www.googleapis.com/auth/drive.appdata",
    "https://www.googleapis.com/auth/drive.scripts",

    # Calendar - Full calendar and meeting management
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.settings.readonly",
    "https://www.googleapis.com/auth/calendar.events.owned",
    "https://www.googleapis.com/auth/calendar.events.owned.readonly",

    # Google Meet - Meeting creation and management
    "https://www.googleapis.com/auth/meetings.space.created",
    "https://www.googleapis.com/auth/meetings.space.settings",
    "https://www.googleapis.com/auth/meetings.space.readonly",

    # Google Docs - Document management
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/documents.readonly",

    # Google Sheets - Spreadsheet management
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/spreadsheets.readonly",

    # Google Slides - Presentation management
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/presentations.readonly",

    # Google Forms - Form management
    "https://www.googleapis.com/auth/forms",
    "https://www.googleapis.com/auth/forms.readonly",

    # Admin Directory - User and organization management
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
    "https://www.googleapis.com/auth/admin.directory.user",
    "https://www.googleapis.com/auth/admin.directory.group.readonly",
    "https://www.googleapis.com/auth/admin.directory.group",
    "https://www.googleapis.com/auth/admin.directory.domain.readonly",
    "https://www.googleapis.com/auth/admin.directory.orgunit.readonly",
    "https://www.googleapis.com/auth/admin.directory.orgunit",
    "https://www.googleapis.com/auth/admin.reports.audit.readonly",
    "https://www.googleapis.com/auth/admin.reports.usage.readonly"
]

GOOGLE_PARSER_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/presentations.readonly",
]

# Optimized service-specific scope mappings
# Each service gets only the minimal scopes it actually needs
GOOGLE_SERVICE_SCOPES = {
    "meet": [
        # Google Meet API v2 - minimal scopes for conference management
        "https://www.googleapis.com/auth/meetings.space.readonly",
        "https://www.googleapis.com/auth/meetings.space.created",
        "https://www.googleapis.com/auth/meetings.space.settings",
        # Calendar API - required for meet.schedule_meeting_with_calendar tool
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/calendar",
    ],
    "gmail": [
        # Gmail API - optimized for common operations
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.compose",
        "https://www.googleapis.com/auth/gmail.send",
    ],
    "drive": [
        # Google Drive API - optimized for file operations
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/drive.metadata.readonly",
    ],
    "calendar": [
        # Google Calendar API - optimized for calendar operations
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/calendar.settings.readonly",
    ],
    "admin": [
        # Google Admin SDK - optimized for directory operations
        "https://www.googleapis.com/auth/admin.directory.user.readonly",
        "https://www.googleapis.com/auth/admin.directory.group.readonly",
        "https://www.googleapis.com/auth/admin.directory.domain.readonly",
        "https://www.googleapis.com/auth/admin.reports.audit.readonly",
    ],
    "sheets": [
        # Google Sheets API - optimized for spreadsheet operations
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/spreadsheets",
    ],
    "docs": [
        # Google Docs API - optimized for document operations
        "https://www.googleapis.com/auth/documents.readonly",
        "https://www.googleapis.com/auth/documents",
    ],
    "slides": [
        # Google Slides API - optimized for presentation operations
        "https://www.googleapis.com/auth/presentations.readonly",
        "https://www.googleapis.com/auth/presentations",
    ],
    "forms": [
        # Google Forms API - optimized for form operations
        "https://www.googleapis.com/auth/forms.readonly",
        "https://www.googleapis.com/auth/forms",
    ],
}

# Services that need parser scopes (for document parsing functionality)
SERVICES_WITH_PARSER_SCOPES = {"drive", "sheets", "docs", "slides", "forms"}

# Services that need additional enterprise scopes
SERVICES_WITH_ENTERPRISE_SCOPES = {"admin", "gmail"}
