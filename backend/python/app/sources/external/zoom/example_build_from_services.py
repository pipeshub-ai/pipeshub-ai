"""Example builder for PipesHub service registry / build_from_services pattern.

This file demonstrates how the Zoom datasource can be wired into the services container.
"""
# zoom unified version

from typing import Any
from app.services.service_builder import service_builder  # type: ignore
from app.services.service_manager import ServiceManager  # type: ignore

from app.sources.client.zoom.zoom import ZoomClient  # type: ignore
from app.sources.external.zoom.zoom import ZoomDataSource  # type: ignore

@service_builder("zoom")
def build_zoom(services: ServiceManager) -> Any:
    """Build the Zoom datasource from the services container.

    Expected:
      - services.get("zoom_rest_client") returns an IClient instance configured for Zoom.
    """
    client = services.get("zoom_rest_client")
    return ZoomDataSource(client)
