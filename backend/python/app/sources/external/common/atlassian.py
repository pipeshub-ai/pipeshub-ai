from dataclasses import dataclass


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
