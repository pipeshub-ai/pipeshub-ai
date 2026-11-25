import contextlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.connectors.core.base.token_service.mcp_token_refresh_service import (  # noqa: F401
        MCPTokenRefreshService,
    )
    from app.connectors.core.base.token_service.token_refresh_service import (  # noqa: F401
        TokenRefreshService,
    )
    from app.connectors.core.base.token_service.toolset_token_refresh_service import (  # noqa: F401
        ToolsetTokenRefreshService,
    )
    from app.connectors.core.registry.oauth_config_registry import (  # noqa: F401
        get_oauth_config_registry,
    )
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider  # noqa: F401

# Which indexing lane a connector is given on Redis Streams, whether an
# administrator may move it, and which pool of lanes an org belongs in. An
# edition can replace any of them: pin a connector to a lane of its own, weight
# lanes, allow admin moves, or keep a group of orgs on lanes of their own.
from app.services.messaging.lanes.assignment_policy import (
    admin_lane_move_allowed as admin_lane_move_allowed,
)
from app.services.messaging.lanes.assignment_policy import (
    choose_connector_lane as choose_connector_lane,
)
from app.services.messaging.lanes.assignment_policy import (
    lane_pool_for_org as lane_pool_for_org,
)

# The edition's services, each imported when first read: a process that asks this module only for its harness hooks
# (the sandbox broker's image check) loaded the Neo4j driver, pandas and the connectors' token services with them,
# about 110 MiB of the idle broker (R25OPS-FU-1).
_LAZY_SERVICES = {
    "TokenRefreshService": "app.connectors.core.base.token_service.token_refresh_service",
    "ToolsetTokenRefreshService": "app.connectors.core.base.token_service.toolset_token_refresh_service",
    "MCPTokenRefreshService": "app.connectors.core.base.token_service.mcp_token_refresh_service",
    "get_oauth_config_registry": "app.connectors.core.registry.oauth_config_registry",
    "Neo4jProvider": "app.services.graph_db.neo4j.neo4j_provider",
}


def __getattr__(name: str) -> Any:
    """Lazy OSS service class exports (avoid circular imports via connector_builder)."""
    if name in _LAZY_SERVICES:
        import importlib

        value = getattr(importlib.import_module(_LAZY_SERVICES[name]), name)
        globals()[name] = value
        return value
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


def admission_backlog() -> "AdmissionBacklog":
    """The recorder of records waiting at each admission stage, one per indexing consumer.

    OSS: counts per tier, stage and lease pool. An edition may return an ``AdmissionBacklog`` built with an
    observer (``entered``/``left`` called with the tier, stage and message) to keep a breakdown of its own.
    """
    from app.services.messaging.consumer_concurrency import AdmissionBacklog

    return AdmissionBacklog()


def indexing_fairness(slot_limit: "Callable[[str], int]") -> "IndexingFairness | None":
    """Optional parts for the record consumer: a key extractor, weights, a slot planner, a buffer room rule, a read
    budget for extra topics, an observer, extra topics and PostgreSQL claim steps.

    ``slot_limit(pool)`` is the slot limit the consumer's own planner is given for a pool; call it only once the
    consumer is built. OSS: none, so the consumer keeps its defaults.
    """
    return None


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
    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    return DataSourceEntitiesProcessor


if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI
    from langchain_core.embeddings import Embeddings
    from langchain_core.language_models.chat_models import BaseChatModel

    from app.agent_loop_lib.hooks.registry import HookRegistry
    from app.agents.agent_loop.context import AgentContext
    from app.agents.registry.toolset_registry import ToolsetRegistry
    from app.services.messaging.consumer_concurrency import AdmissionBacklog
    from app.services.messaging.scheduling.interface import IndexingFairness
    from app.services.model_identity.forwarders.registry import ForwarderRegistry
    from app.services.model_identity.groups import GroupMembershipResolver
    from app.web.ports import WebAccessGate
    from harness_core.edition import HarnessEdition
    from harness_core.tools.registry import ToolRegistry


def register_extra_toolsets(registry: "ToolsetRegistry") -> None:
    """Register edition-specific agent toolsets. OSS: no-op."""
    pass


def register_extra_agent_hooks(hooks: "HookRegistry", context: "AgentContext") -> None:
    """Hooks an edition adds to an agent turn. Both loops build their hooks through
    ``PipesHubAgentFactory._build_hooks``, which calls this after the completion gate and before the last
    POST_MODEL check and the per-tool approval gate. OSS: none."""
    pass


def extend_tool_grant(tool_names: list[str], context: "AgentContext", registry: "ToolRegistry") -> list[str]:
    """The tools an agent turn is granted, after the session's own grant. OSS: unchanged."""
    return tool_names


def split_record_continuation(
    record_ids: list[str], start_block: int, tool_state: dict[str, Any]
) -> list[tuple[list[str], int]] | None:
    """How one fetch that continues several records at one ``start_block`` is read: groups of ids, each with the
    block its read starts at, or ``None`` to refuse the call and ask for one record at a time. OSS: refuse, since
    one offset applied to every id re-reads whichever stopped later and skips the start of the others."""
    return None


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



def harness_edition() -> "HarnessEdition":
    """The ports an edition supplies to the agent harness (``harness_core.edition``). OSS: none, so every
    harness port is its default."""
    from harness_core.edition import NO_HARNESS_EDITION

    return NO_HARNESS_EDITION


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
