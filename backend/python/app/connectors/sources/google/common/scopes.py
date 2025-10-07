GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]

# GOOGLE_CONNECTOR_ENTERPRISE_SCOPES = [
#     "https://www.googleapis.com/auth/gmail.readonly",
#     "https://www.googleapis.com/auth/drive.readonly",
#     "https://www.googleapis.com/auth/calendar.readonly",
#     "https://www.googleapis.com/auth/admin.directory.user.readonly",
#     "https://www.googleapis.com/auth/admin.directory.group.readonly",
#     "https://www.googleapis.com/auth/admin.directory.domain.readonly",
#     "https://www.googleapis.com/auth/admin.reports.audit.readonly",
#     "https://www.googleapis.com/auth/admin.directory.orgunit",
# ]


GOOGLE_CONNECTOR_ENTERPRISE_SCOPES = [
    # Gmail - Enterprise email management
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.settings.basic",  # Email settings

    # Drive - Enterprise file management
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly",

    # Calendar - Enterprise calendar management
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.settings.readonly",

    # Admin Directory - User and organization management
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
    "https://www.googleapis.com/auth/admin.directory.user",  # For user management
    "https://www.googleapis.com/auth/admin.directory.group.readonly",
    "https://www.googleapis.com/auth/admin.directory.group",  # For group management
    "https://www.googleapis.com/auth/admin.directory.domain.readonly",
    "https://www.googleapis.com/auth/admin.directory.orgunit.readonly",
    "https://www.googleapis.com/auth/admin.directory.orgunit",
    "https://www.googleapis.com/auth/admin.reports.audit.readonly",
]

GOOGLE_PARSER_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/presentations.readonly",
]
