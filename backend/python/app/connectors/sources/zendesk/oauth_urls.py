"""Where Zendesk sign-in, code exchange and token refresh may send the client secret.

Zendesk's OAuth endpoints are fixed per subdomain, so the saved Authorize and Token
URL fields are never trusted: every request goes to https://<subdomain>.zendesk.com.
"""

import re
from typing import Any
from urllib.parse import urlparse

from app.connectors.core.constants import AuthFieldKeys
from app.sources.client.zendesk.zendesk import normalize_subdomain
from app.utils.oauth_config import OAuthUrlRule, register_oauth_url_rule

ZENDESK_OAUTH_URL_ERROR = (
    "Enter only your Zendesk subdomain, such as acme for acme.zendesk.com. The Authorize "
    "and Token URLs, if given, must be https addresses on zendesk.com."
)
SUBDOMAIN_KEY = "subdomain"
_ZENDESK_DOMAIN = ".zendesk.com"
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_HTTPS_DEFAULT_PORT = 443


def _zendesk_host(settings: dict[str, Any]) -> str:
    subdomain = normalize_subdomain(settings.get(SUBDOMAIN_KEY))
    if not _DNS_LABEL.fullmatch(subdomain):
        raise ValueError(ZENDESK_OAUTH_URL_ERROR)
    return f"{subdomain}{_ZENDESK_DOMAIN}"


def check_zendesk_oauth_settings(settings: dict[str, Any]) -> None:
    """Raise ValueError when a saved subdomain or OAuth URL is not a Zendesk https host.

    Only the fields present are checked: a connector linked to an existing OAuth app
    carries none of them. The exact host is enforced by ``pin_zendesk_oauth_urls``.
    """
    if settings.get(SUBDOMAIN_KEY) not in (None, ""):
        _zendesk_host(settings)
    for key in (AuthFieldKeys.AUTHORIZE_URL, AuthFieldKeys.TOKEN_URL):
        url = settings.get(key)
        if not url:
            continue
        try:
            parsed = urlparse(str(url).strip())
            port = parsed.port
        except ValueError:
            raise ValueError(ZENDESK_OAUTH_URL_ERROR) from None
        url_host = (parsed.hostname or "").lower()
        if (
            parsed.scheme.lower() != "https"
            or parsed.username is not None
            or parsed.password is not None
            or port not in (None, _HTTPS_DEFAULT_PORT)
            or not url_host.endswith(_ZENDESK_DOMAIN)
            or not _DNS_LABEL.fullmatch(url_host.removesuffix(_ZENDESK_DOMAIN))
        ):
            raise ValueError(ZENDESK_OAUTH_URL_ERROR)


def pin_zendesk_oauth_urls(flow_config: dict[str, Any]) -> None:
    host = _zendesk_host(flow_config)
    flow_config[AuthFieldKeys.AUTHORIZE_URL] = f"https://{host}/oauth/authorizations/new"
    flow_config[AuthFieldKeys.TOKEN_URL] = f"https://{host}/oauth/tokens"


register_oauth_url_rule(
    "Zendesk",
    OAuthUrlRule(check=check_zendesk_oauth_settings, pin=pin_zendesk_oauth_urls, settings=(SUBDOMAIN_KEY,)),
)
