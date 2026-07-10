import logging
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import urlparse


class AtlassianMultiSiteError(ValueError):
    """OAuth token can reach multiple Atlassian sites. A single-site
    (resource-restricted) OAuth app is required. Subclasses ``ValueError`` so
    existing broad handlers keep working."""


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


def dedupe_atlassian_cloud_resources(
    resources: list[AtlassianCloudResource],
) -> list[AtlassianCloudResource]:
    """Collapse duplicate accessible-resources entries for the same cloud site.

    Atlassian sometimes returns the same site more than once (same cloud id /
    hostname) when multiple products share that site. Count unique sites only.
    """
    unique: list[AtlassianCloudResource] = []
    seen: set[str] = set()
    for r in resources:
        key = (r.id or "").strip() or atlassian_site_hostname(r.url)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(r)
    return unique


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
    """Return ``preferred_site`` if set; otherwise resolve the site from the OAuth
    token's accessible resources.

    With no ``baseUrl`` configured we only auto-resolve when the token is scoped to
    exactly one site (resource-restricted OAuth apps, and the SaaS default app). A
    token that can reach multiple sites is ambiguous without a ``baseUrl``, so we
    raise instead of silently picking an arbitrary site. Raises ValueError if the
    token has zero accessible sites."""
    if logger is None:  # factories type their logger as optional; stay safe
        logger = logging.getLogger(__name__)
    if preferred_site:
        return preferred_site
    resources = await get_accessible_resources(access_token)
    logger.debug(
        "%s: accessible-resources returned %d site(s): %s",
        product,
        len(resources),
        [{"id": r.id, "name": r.name, "url": r.url} for r in resources],
    )
    resources = dedupe_atlassian_cloud_resources(resources)
    if not resources:
        raise ValueError(
            f"{product}: No accessible Atlassian sites for this OAuth token."
        )
    if len(resources) > 1:
        raise AtlassianMultiSiteError(
            f"This OAuth app has access to multiple {product} sites. "
            "Create a single-site (resource-restricted) OAuth app in the Atlassian "
            "Developer Console, then reconnect."
        )
    logger.info(
        "%s: using single accessible site (%s)",
        product, resources[0].url,
    )
    return resources[0].url
