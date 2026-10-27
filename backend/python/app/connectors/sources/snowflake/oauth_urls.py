"""Build Snowflake OAuth endpoints from the configured account identifier.

Snowflake OAuth endpoints are account-specific.  The connector registry cannot
know the account when it is imported, so it exposes a harmless placeholder and
this rule replaces it immediately before authorization, callback, and refresh.
"""

import re
from typing import Any
from urllib.parse import urlparse

from app.connectors.core.constants import AuthFieldKeys
from app.utils.oauth_config import OAuthUrlRule, register_oauth_url_rule

ACCOUNT_IDENTIFIER_KEY = "accountIdentifier"
SNOWFLAKE_ACCOUNT_DOMAIN = ".snowflakecomputing.com"
SNOWFLAKE_OAUTH_BASE_URL = "https://{account}.snowflakecomputing.com"
SNOWFLAKE_OAUTH_URL_ERROR = (
    "Enter a Snowflake account identifier, such as myorg-account_xyz or "
    "https://myorg-account_xyz.snowflakecomputing.com."
)

_ACCOUNT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def snowflake_account_host(settings: dict[str, Any]) -> str:
    """Return the canonical Snowflake account host for OAuth requests."""
    value = settings.get(ACCOUNT_IDENTIFIER_KEY)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(SNOWFLAKE_OAUTH_URL_ERROR)

    raw = value.strip()
    parsed_value = raw if "://" in raw else f"https://{raw}"
    try:
        parsed = urlparse(parsed_value)
        port = parsed.port
    except ValueError:
        raise ValueError(SNOWFLAKE_OAUTH_URL_ERROR) from None

    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme.lower() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.params
        or parsed.query
        or parsed.fragment
        or not host
    ):
        raise ValueError(SNOWFLAKE_OAUTH_URL_ERROR)

    account = host.removesuffix(SNOWFLAKE_ACCOUNT_DOMAIN)
    if not _ACCOUNT_NAME.fullmatch(account):
        raise ValueError(SNOWFLAKE_OAUTH_URL_ERROR)
    return f"{account}{SNOWFLAKE_ACCOUNT_DOMAIN}"


def check_snowflake_oauth_settings(settings: dict[str, Any]) -> None:
    """Validate a Snowflake account identifier when one is supplied."""
    if ACCOUNT_IDENTIFIER_KEY in settings:
        snowflake_account_host(settings)


def pin_snowflake_oauth_urls(flow_config: dict[str, Any]) -> None:
    """Replace registry or saved URLs with the account-specific endpoints."""
    host = snowflake_account_host(flow_config)
    flow_config[AuthFieldKeys.AUTHORIZE_URL] = f"https://{host}/oauth/authorize"
    flow_config[AuthFieldKeys.TOKEN_URL] = f"https://{host}/oauth/token-request"


register_oauth_url_rule(
    "Snowflake",
    OAuthUrlRule(
        check=check_snowflake_oauth_settings,
        pin=pin_snowflake_oauth_urls,
        settings=(ACCOUNT_IDENTIFIER_KEY,),
    ),
)
