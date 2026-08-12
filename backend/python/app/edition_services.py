"""Single flip file for all low-level OSS↔EE service switches.

Flip OSS vs EE imports here together with ``app.edition_config``.
Kept separate from ``edition_config`` so modules loaded before the API
routes stack (startup_service, kafka utils, connector builders) can import
without triggering ``edition_config``'s api.routes.* circular imports.

Heavy classes that pull ``connector_builder`` (EventService, Kafka handlers,
ConnectorRegistry) are resolved lazily so ``get_oauth_config_registry`` stays
safe to import from ``connector_builder``.

EE: uncomment the EE lines and comment out the matching OSS lines.
"""

from typing import Any

# OSS — only imports safe for connector_builder / startup_service callers
from app.connectors.core.base.token_service.token_refresh_service import (
    TokenRefreshService,
)
from app.connectors.core.base.token_service.toolset_token_refresh_service import (
    ToolsetTokenRefreshService,
)
from app.connectors.core.registry.oauth_config_registry import (
    get_oauth_config_registry,
)


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


# EE — uncomment all EE lines together (and comment out the matching OSS lines /
# the OSS __getattr__ / get_connector_registry_cls / hooks above).
#
# from app.ee.connectors.core.base.token_service.token_refresh_service import TokenRefreshService
# from app.ee.connectors.core.base.token_service.toolset_token_refresh_service import ToolsetTokenRefreshService
# from app.ee.connectors.core.registry.oauth_config_registry import get_oauth_config_registry
#
# def __getattr__(name: str) -> Any:
#     if name == "EventService":
#         from app.ee.connectors.services.event_service import EventService
#         return EventService
#     if name == "EntityEventService":
#         from app.ee.services.messaging.kafka.handlers.entity import EntityEventService
#         return EntityEventService
#     if name == "RecordEventHandler":
#         from app.ee.services.messaging.kafka.handlers.record import RecordEventHandler
#         return RecordEventHandler
#     raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
#
# from app.ee.config.configuration_service_ee import ConfigurationServiceEE
# from app.ee.connectors.core.base.data_store.graph_data_store import GraphDataStore
# from app.ee.connectors.core.factory.ee_connector_overrides import (
#     register_ee_connectors as _register_ee_connectors,
# )
#
#
# def get_connector_registry_cls():
#     from app.ee.connectors.core.registry.connector_registry import ConnectorRegistry
#     return ConnectorRegistry
#
#
# async def scope_org_resources(app_container, data_store, org_id):
#     cfg = ConfigurationServiceEE.for_org(
#         app_container.config_service().get_raw_service(), org_id
#     )
#     store = GraphDataStore(
#         app_container.logger(), data_store.graph_provider, org_id=org_id
#     )
#     return cfg, store
#
#
# def register_extra_connectors() -> None:
#     _register_ee_connectors()
#
#
# def get_startup_extra_kwargs(app_container) -> dict:
#     return {"oauth_config_resolver": app_container.oauth_config_resolver()}
#
#
# async def pre_sync_hook(app_container, logger) -> None:
#     from app.connectors.core.base.token_service.startup_service import startup_service
#     svc = startup_service.get_token_refresh_service()
#     if svc:
#         try:
#             await svc._refresh_all_tokens()
#             logger.info("✅ Pre-startup token refresh completed")
#         except Exception as e:
#             logger.warning(f"⚠️ Pre-startup token refresh failed (non-fatal): {e}")
