import contextlib
from typing import TYPE_CHECKING, Any

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
# Which indexing lane a connector is given on Redis Streams, and whether an
# administrator may move it. An edition can replace either: pin a connector to
# a lane of its own, weight lanes, or allow admin moves.
from app.services.messaging.lanes.assignment_policy import (
    admin_lane_move_allowed as admin_lane_move_allowed,
)
from app.services.messaging.lanes.assignment_policy import (
    choose_connector_lane as choose_connector_lane,
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


def get_data_entities_processor_cls():
    """Return the DataSourceEntitiesProcessor class for this edition."""
    from app.connectors.core.base.data_processor.data_source_entities_processor import DataSourceEntitiesProcessor
    return DataSourceEntitiesProcessor


if TYPE_CHECKING:
    from fastapi import FastAPI
    from langchain_core.embeddings import Embeddings
    from langchain_core.language_models.chat_models import BaseChatModel

    from app.agents.registry.toolset_registry import ToolsetRegistry
    from app.services.model_identity.forwarders.registry import ForwarderRegistry
    from app.services.model_identity.groups import GroupMembershipResolver
    from app.web.ports import WebAccessGate


def register_extra_toolsets(registry: "ToolsetRegistry") -> None:
    """Register edition-specific agent toolsets. OSS: no-op."""
    pass


def configure_app(app: "FastAPI", service: str) -> None:
    """Edition setup on a freshly built FastAPI app. `service` names the process. OSS: no-op."""
    pass


def decorate_chat_model(model: "BaseChatModel", provider: str, model_name: str, config: dict) -> "BaseChatModel":
    """Wrap a chat model with edition extras. OSS: none."""
    return model


def decorate_embeddings(embeddings: "Embeddings", provider: str, model_name: str, config: dict) -> "Embeddings":
    """Wrap an embeddings client with edition extras. OSS: none."""
    return embeddings


def get_group_membership_resolver(container: Any) -> "GroupMembershipResolver":  # noqa: ANN401 - an untyped dependency-injection object
    """Resolves a person's IdP group ids for identity forwarding to a model gateway.

    OSS knows no groups: the gateway receives the user id only.
    """
    from app.services.model_identity.groups import NoopGroupResolver

    return NoopGroupResolver()


def register_identity_forwarders(registry: "ForwarderRegistry") -> None:
    """Add edition-specific wire formats (for example signed JWT) to the forwarder registry. OSS: none."""
    pass


def get_web_access_gate() -> "WebAccessGate":
    """Decides which web searches and fetches may happen. OSS: any search, and any http(s) URL
    whose address is public (checked again for every redirect when it is fetched)."""
    from app.web.gate import DefaultWebAccessGate

    return DefaultWebAccessGate()



# Multi-worker sync needs exclusion across processes, which this build does not
# have. It therefore pins the service to one worker: raising
# CONNECTOR_UVICORN_WORKERS here has never been safe, because exclusion would be
# a per-process dict plus a fail-open database read.


def max_connector_workers() -> int:
    """Always one. Multi-worker sync needs exclusion this build lacks."""
    return 1


async def create_coordinator(logger, config_service):
    """One worker, so an in-process registry is already an exact answer."""
    from app.connectors.core.sync.sync_coordinator import LocalSyncCoordinator

    return LocalSyncCoordinator(logger)


def bootstrap_guard(logger, config_service, **kwargs):
    """One process, so it is always the one that should bootstrap."""
    @contextlib.asynccontextmanager
    async def _single_process():
        yield True

    return _single_process()


async def bootstrap_leased_ids(connector_ids: list[str]) -> set[str]:
    """One process, so a restart has no live peer whose flag must stay."""
    return set()


def build_sync_dispatcher(logger, producer):
    """Syncs always run in this process."""
    from app.connectors.core.sync.sync_dispatcher import SyncEventDispatcher

    logger.info("Sync dispatch mode: inprocess")
    return SyncEventDispatcher(logger, producer)


def start_sync_reaper(graph_provider, coordinator, logger):
    """Nothing to reap: a single process owns all of its own sync state."""
    return None


async def stop_sync_reaper(task) -> None:
    return None


def sync_executor_enabled() -> bool:
    return False
