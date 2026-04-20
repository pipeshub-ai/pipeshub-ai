from dataclasses import dataclass
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
