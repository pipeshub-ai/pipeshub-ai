import logging
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import urlparse


@dataclass
class AtlassianCloudResource:
    """Represents an Atlassian Cloud resource (site)

    Args:
        id: The ID of the resource
        name: The name of the resource
        url: The URL of the resource
        scopes: The scopes of the resource
        avatar_url: The avatar URL of the resource

    """

    id: str
    name: str
    url: str
    scopes: list[str]
    avatar_url: str | None = None


def atlassian_site_hostname(site_url: str) -> str:
    """Lowercase hostname for an Atlassian site URL (with or without scheme)."""
    s = (site_url or "").strip().rstrip("/")
    if not s:
        return ""
    if not s.startswith(("http://", "https://")):
        s = f"https://{s}"
    return (urlparse(s).hostname or "").lower()


def match_atlassian_cloud_resource(
    resources: list[AtlassianCloudResource],
    base_url: str,
    *,
    product: str,
) -> AtlassianCloudResource:
    """Return the accessible resource whose site URL hostname matches ``base_url``."""
    if not resources:
        raise ValueError(f"{product}: No Atlassian Cloud sites returned for this Site URL.")
    site = (base_url or "").strip()
    if not site:
        raise ValueError(f"{product}: Atlassian site URL (baseUrl) is required.")
    preferred_host = atlassian_site_hostname(site)
    if not preferred_host:
        raise ValueError(f"{product}: Atlassian site URL (baseUrl) is required.")
    for r in resources:
        if atlassian_site_hostname(r.url) == preferred_host:
            return r
    raise ValueError(
        f"{product}: This token has no access to that site ({preferred_host}). "
        "Check baseUrl matches the site you authorized."
    )


async def resolve_preferred_site_with_fallback(
    preferred_site: str,
    access_token: str,
    get_accessible_resources: Callable[[str], Awaitable[list[AtlassianCloudResource]]],
    logger: logging.Logger,
    product: str,
) -> str:
    """Return ``preferred_site`` if set; otherwise fall back to the first accessible
    resource returned by the OAuth token. Raises ValueError only if the token has
    zero accessible sites. Unblocks legacy connectors/toolsets whose configs predate
    the ``baseUrl`` requirement."""
    if preferred_site:
        return preferred_site
    resources = await get_accessible_resources(access_token)
    if not resources:
        raise ValueError(
            f"Atlassian site URL (baseUrl) missing and OAuth token has no accessible {product} sites"
        )
    logger.warning(
        "%s baseUrl missing from config; using accessible-resources[0] (%s)",
        product, resources[0].url,
    )
    return resources[0].url
