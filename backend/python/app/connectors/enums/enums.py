from enum import Enum


class AccountType(Enum):
    INDIVIDUAL = "INDIVIDUAL"
    ENTERPRISE = "ENTERPRISE"
    BUSINESS = "BUSINESS"

class ConnectorStatus(Enum):
    NOT_STARTED = "NOT_STARTED"
    PAUSED = "PAUSED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    FILE_TYPE_NOT_SUPPORTED = "FILE_TYPE_NOT_SUPPORTED"
    AUTO_INDEX_OFF = "AUTO_INDEX_OFF"

class ConnectorType(Enum):
    """Enumeration of connector types"""
    GOOGLE_DRIVE = "google_drive"
    GOOGLE_GMAIL = "google_gmail"
    GOOGLE_CALENDAR = "google_calendar"
    MICROSOFT_ONEDRIVE = "microsoft_onedrive"
    MICROSOFT_OUTLOOK = "microsoft_outlook"
    MICROSOFT_SHAREPOINT = "microsoft_sharepoint"
    DROPBOX = "dropbox"
    BOX = "box"
    SLACK = "slack"
    MICROSOFT_TEAMS = "microsoft_teams"
    DISCORD = "discord"
    NOTION = "notion"
    CONFLUENCE = "confluence"
    JIRA = "jira"
    TRELLO = "trello"
    ASANA = "asana"
    CLICKUP = "clickup"
    FIGMA = "figma"
    ZOOM = "zoom"
    SALESFORCE = "salesforce"
    HUBSPOT = "hubspot"
    ZENDESK = "zendesk"
    INTERCOM = "intercom"
    GITHUB = "github"
    GITLAB = "gitlab"
    BITBUCKET = "bitbucket"
    LINEAR = "linear"
    CLICKHOUSE = "clickhouse"
    SNOWFLAKE = "snowflake"
    DATABRICKS = "databricks"
    S3 = "s3"
    # Add more as needed


class AuthenticationType(Enum):
    """Enumeration of authentication types"""
    OAUTH2 = "oauth2"
    API_KEY = "api_key"
    BEARER_TOKEN = "bearer_token"
    BASIC_AUTH = "basic_auth"
    SERVICE_ACCOUNT = "service_account"
    SAML = "saml"
    LDAP = "ldap"


class SyncStatus(Enum):
    """Enumeration of sync statuses"""
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    PARTIAL = "partial"
