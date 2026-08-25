from typing import Any

from app.connectors.core.base.token_service.token_refresh_service import (
    TokenRefreshService,
)
from app.connectors.core.base.token_service.toolset_token_refresh_service import (
    ToolsetTokenRefreshService,
)
from app.connectors.core.base.token_service.mcp_token_refresh_service import (
    MCPTokenRefreshService,
)
from app.connectors.core.registry.oauth_config_registry import (
    get_oauth_config_registry,
)
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def __getattr__(name: str) -> Any:
    """Lazy OSS service class exports (avoid circular imports via connector_builder)."""
    if name == "EventService":
        from app.connectors.services.event_service import EventService

        return EventService
    if name == "EntityEventService":
        from app.services.messaging.kafka.handlers.entity import EntityEventService

        return EntityEventService
    if name == "RecordEventHandler":
        from app.services.messaging.kafka.handlers.record import RecordEventHandler

        return RecordEventHandler
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_connector_registry_cls():
    """Return the ConnectorRegistry class (lazy to avoid circular imports)."""
    from app.connectors.core.registry.connector_registry import ConnectorRegistry

    return ConnectorRegistry


async def scope_org_resources(app_container, data_store, org_id):
    """Return (config_service, data_store) for connector init in an org.

    OSS: global config + shared data store (org_id passed separately to factory).
    """
    return app_container.config_service(), data_store


def register_extra_connectors() -> None:
    """Register edition-specific connector class overrides. OSS: no-op."""
    pass


def get_startup_extra_kwargs(app_container) -> dict:
    """Extra kwargs for startup_service.initialize(). OSS: none."""
    return {}


async def pre_sync_hook(app_container, logger) -> None:
    """Run before resume_sync_services in post-startup. OSS: no-op."""
    pass


def get_data_entities_processor_cls():
    """Return the DataSourceEntitiesProcessor class for this edition."""
    from app.connectors.core.base.data_processor.data_source_entities_processor import DataSourceEntitiesProcessor
    return DataSourceEntitiesProcessor

