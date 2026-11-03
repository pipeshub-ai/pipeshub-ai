"""Web connector package."""

from typing import Any

__all__ = ["WebConnector"]


def __getattr__(name: str) -> Any:  # noqa: ANN401
    # Lazy, so importing a light module here (url_identity, address_guard) doesn't load the connector.
    if name == "WebConnector":
        from app.connectors.sources.web.connector import WebConnector

        return WebConnector
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
