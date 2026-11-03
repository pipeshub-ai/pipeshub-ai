"""Firecrawl, cloud or self-hosted: one server that both searches and reads pages."""

from typing import Any

from app.web.endpoint import Endpoint

CLOUD_URL = "https://api.firecrawl.dev"
NAME = "Firecrawl"


def firecrawl_endpoint(configuration: dict[str, Any], source: str) -> Endpoint:
    """The configured server, else Firecrawl's cloud. The cloud needs an API key; a self-hosted
    server only when it was set up with one."""
    return Endpoint(
        str(configuration.get("baseUrl") or CLOUD_URL), str(configuration.get("apiKey") or ""), source=source, name=NAME,
    )
