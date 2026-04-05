"""Constants specific to Microsoft connectors (OAuth URLs, Graph scopes, API field names)."""


class MicrosoftOAuth:
    """Microsoft identity / OAuth endpoints."""
    BASE_URL = "https://login.microsoftonline.com"
    OAUTH_PATH = "/oauth2/v2.0/authorize"
    TOKEN_PATH = "/oauth2/v2.0/token"
    COMMON_TENANT = "common"
    VERSION = "v2.0"
    
    @staticmethod
    def authorize_url(tenant: str = "common") -> str:
        """Full OAuth2 authorize URL for the given Azure AD tenant."""
        return f"{MicrosoftOAuth.BASE_URL}/{tenant}{MicrosoftOAuth.OAUTH_PATH}"
    
    @staticmethod
    def token_url(tenant: str = "common") -> str:
        """Full OAuth2 token URL for the given Azure AD tenant."""
        return f"{MicrosoftOAuth.BASE_URL}/{tenant}{MicrosoftOAuth.TOKEN_PATH}"


class MicrosoftOAuthParams:
    """OAuth additional parameters (Azure AD / MSAL-style)."""
    RESPONSE_MODE_QUERY = "query"
    PROMPT_SELECT_ACCOUNT = "select_account"
    PROMPT_CONSENT = "consent"


class MicrosoftGraphScopes:
    """Microsoft Graph delegated scopes."""
    MAIL_READ = "https://graph.microsoft.com/Mail.Read"
    USER_READ = "https://graph.microsoft.com/User.Read"
    OFFLINE_ACCESS = "offline_access"


class MicrosoftGraphFields:
    """Microsoft Graph / JSON field names (camelCase as returned by API)."""
    ID = "id"
    DISPLAY_NAME = "displayName"
    SUBJECT = "subject"
    HAS_ATTACHMENTS = "hasAttachments"
    VALUE = "value"
    ODATA_NEXT_LINK = "@odata.nextLink"
    ODATA_DELTA_LINK = "@odata.deltaLink"
    EMAIL_ADDRESS = "emailAddress"
    ADDRESS = "address"


def escape_odata_string(value: str) -> str:
    """
    Escape single quotes for OData string literals per OData v4 spec.
    
    In OData filter expressions, string literals are enclosed in single quotes.
    Single quotes within the string must be escaped by doubling them ('').
    
    Args:
        value: The string value to escape
        
    Returns:
        Escaped string safe for use in OData filter expressions
        
    Example:
        >>> escape_odata_string("O'Brien")
        "O''Brien"
        >>> escape_odata_string("Normal text")
        "Normal text"
    """
    return value.replace("'", "''")
