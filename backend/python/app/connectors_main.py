import app.utils.runtime_threads  # noqa: E402 - must precede all ML library imports

import asyncio
import logging
import os
import traceback
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.edition_config import (
    authMiddleware,
    connector_router,
    ensure_org_context,
    knowledge_hub_service_factory,
    oauth_apps_router,
    sharing_router,
)
from app.api.middlewares.request_context import RequestContextMiddleware
from app.modules.demo_data.router import demo_data_router
from app.utils.logger import install_log_redaction
from app.utils.request_context import set_service_suffix

set_service_suffix("-cs")
from app.agents.mcp.registry import get_mcp_registry
from app.agents.registry.toolset_registry import get_toolset_registry
from app.api.routes.api_toolsets import router as api_toolsets_router  # noqa: E402 - after set_service_suffix, like its neighbours
from app.api.routes.entity import router as entity_router
from app.api.routes.mcp_servers import router as mcp_servers_router
from app.api.routes.toolsets import router as toolsets_router
from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import AccountType, AppStatus, CollectionNames
from app.config.constants.service import config_node_constants
from app.connectors.core.base.connector.instance_lock import connector_init_lock
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.core.base.token_service.startup_service import startup_service
from app.connectors.core.constants import ConnectorStateKeys, SyncRunOutcome
from app.connectors.core.factory.connector_factory import ConnectorFactory
from app.connectors.core.sync.sync_dispatcher import (
    SyncSpec,
    get_dispatcher,
    set_dispatcher,
)
from app.connectors.core.sync.sync_coordinator import (
    executor_identity,
    get_coordinator,
    process_identity,
    set_coordinator,
)
from app.connectors.core.sync.sync_runner import (
    drain_queued_syncs,
    join_cleanup_tasks,
    resumes_interrupted_full_sync,
)
from app.connectors.core.sync.task_manager import (
    code_edge_build_task_manager,
    reindex_task_manager,
)
from app.connectors.core.thread_pool import get_shared_connector_thread_pool
from app.connectors.api.artifacts_router import artifacts_router
from app.connectors.sources.localKB.api.kb_router import kb_router
from app.connectors.sources.localKB.api.knowledge_hub_router import (
    get_knowledge_hub_service,
    knowledge_hub_router,
)
from app.containers.connector import initialize_container
from app.edition_containers import ConnectorAppContainer
from app.edition_services import (
    build_sync_dispatcher,
    configure_app,
    create_coordinator,
    get_connector_registry_cls,
    get_data_entities_processor_cls,
    get_oauth_config_registry,
    get_startup_extra_kwargs,
    max_connector_workers,
    pre_sync_hook,
    register_extra_connectors,
    scope_org_resources,
    start_sync_reaper,
    stop_sync_reaper,
    sync_executor_enabled,
)
from app.modules.code_graph.edge_build_reconciler import reconcile_forever  # noqa: E402 - after set_service_suffix(), like the imports around it
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider  # noqa: E402 - like the imports around it, after set_service_suffix()
from app.services.messaging.config import ConsumerType, MessageBrokerType, Topic, get_message_broker_type
from app.services.messaging.kafka.utils.utils import KafkaUtils
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.messaging.utils import MessagingUtils
from app.telemetry.flag import telemetry_enabled  # noqa: E402 - like the imports around it, after set_service_suffix()
from app.telemetry.modules.connector_metrics import set_connector_active
from app.telemetry.setup import setup_telemetry
from app.utils.process_hardening import mark_process_non_dumpable
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.utils.user_messages import SOMETHING_WENT_WRONG

container = ConnectorAppContainer.init("connector_service")

async def get_initialized_container() -> ConnectorAppContainer:
    """Dependency provider for initialized container"""
    # Create container instance
    if not hasattr(get_initialized_container, "_initialized"):
        await initialize_container(container)
        # Wire the container after initialization
        container.wire(
            modules=[
                "app.core.celery_app",
                "app.connectors.api.router",
                "app.api.routes.toolsets",
                "app.connectors.sources.localKB.api.kb_router",
                "app.connectors.sources.localKB.api.knowledge_hub_router",
                "app.api.routes.entity",
                "app.connectors.api.middleware",
                "app.core.signed_url",
            ]
        )
        setattr(get_initialized_container, "_initialized", True)
    return container


async def refresh_toolset_tokens(app_container: ConnectorAppContainer) -> bool:
    """
    Refresh OAuth tokens for all authenticated toolsets.
    Uses the dedicated toolset token refresh service.
    This function triggers an initial refresh on startup, but the service
    also runs periodic refreshes automatically.
    """
    logger = app_container.logger()
    logger.info("🔄 Triggering initial toolset token refresh...")

    try:
        toolset_refresh_service = startup_service.get_toolset_token_refresh_service()

        if not toolset_refresh_service:
            logger.warning("⚠️ Toolset token refresh service not initialized, skipping toolset token refresh")
            return False

        # The toolset refresh service handles all the logic internally
        # Just trigger the refresh all tokens method
        await toolset_refresh_service._refresh_all_tokens()
        logger.info("✅ Initial toolset token refresh completed")
        return True

    except Exception as e:
        logger.error(f"❌ Error refreshing toolset tokens: {e}", exc_info=True)
        return False


async def initialize_sync_coordinator(
    app_container: ConnectorAppContainer, logger: logging.Logger
) -> None:
    """Install the process's lease manager before anything can start a sync.

    Always installed, so the start path is a single unconditional `begin` with
    no window where nothing guards at all. Which implementation is installed
    comes from `edition_services.create_coordinator`; here it is the in-process
    one, exact because this build runs a single sync worker.
    """
    manager = await create_coordinator(logger, app_container.config_service())
    set_coordinator(manager)
    logger.info(
        "✅ Sync lease manager: %s (ttl=%ss, heartbeat=%ss, instance=%s)",
        type(manager).__name__, manager.ttl_sec, manager.heartbeat_sec,
        manager.instance_id,
    )


async def reset_stale_sync_state(graph_provider, logger: logging.Logger) -> None:
    """Reset sync state left behind by a crash.

    Anything holding a live lease is syncing right now on some process;
    everything else marked SYNCING/FULL_SYNCING or isLocked=True is stale.
    Without this, a crash mid-sync leaves a connector showing SYNCING forever,
    and a crash inside the full-sync prep window leaves isLocked=True, which
    409s resync/config/delete with no way back short of editing the database.

    The lease filter is what makes this safe once syncs run outside this
    process: the old premise was "single-process service, nothing can be syncing
    at boot", and an API restart under that assumption would reset every sync
    running elsewhere to IDLE — after which the start guard reads IDLE and lets a
    second one begin.

    Never touches DELETING — that doc is mid-deletion, not mid-sync.

    A stale FULL_SYNCING also gets pendingFullSync. That sync's prep deleted the
    connector's sync points before it was cut off, and what the source no longer
    has is only unlinked once a full sync runs to the end. Reset to a bare IDLE,
    the connector looked healthy and its next sync was incremental, which never
    does that cleanup. With the flag, the boot resume below runs it again in full.

    It also gets pendingResync: nothing finished that run, so the request it
    was serving is still owed, and that is what lets the boot resume restart it
    on a MANUAL connector too (see ``resumes_interrupted_full_sync``).
    """
    try:
        # Both lookups go through get_nodes_by_field_in: it is the one that
        # translates _key back to "id" on both providers (Arango stores _key,
        # so a filters-based lookup would hand back {"id": None} there).
        stuck = await graph_provider.get_nodes_by_field_in(
            CollectionNames.APPS.value,
            "status",
            [AppStatus.SYNCING.value, AppStatus.FULL_SYNCING.value],
            ["id", "status"],
        )
        locked = await graph_provider.get_nodes_by_field_in(
            CollectionNames.APPS.value,
            "isLocked",
            [True],
            ["id", "status"],
        )

        stuck_keys = {doc["id"] for doc in (stuck or []) if doc.get("id")}
        full_sync_keys = {
            doc["id"]
            for doc in (stuck or [])
            if doc.get("id") and doc.get("status") == AppStatus.FULL_SYNCING.value
        }
        # A locked doc mid-deletion keeps its DELETING status; only the stuck
        # lock is released for it.
        locked_only = {
            doc["id"]: doc.get("status")
            for doc in (locked or [])
            if doc.get("id") and doc["id"] not in stuck_keys
        }
        if not stuck_keys and not locked_only:
            return

        # Skip anything genuinely running elsewhere.
        coordinator = get_coordinator()
        if coordinator is not None and not getattr(
            coordinator, "reports_liveness", True
        ):
            # peek_many answers "nothing is live", not "nothing is". That is
            # only good enough when this process is the only one that can sync;
            # otherwise this would reset a sync running elsewhere to IDLE, and the
            # start guard would then read IDLE and let a second one begin.
            if max_connector_workers() > 1 or sync_executor_enabled():
                logger.warning(
                    "Startup sweep skipped: sync leases are unavailable, so a "
                    "running sync on another process cannot be told from a stale one."
                )
                return
        if coordinator is not None:
            try:
                live = await coordinator.peek_many(stuck_keys | set(locked_only))
            except Exception as e:
                # Fail safe: a sweep that cannot tell live from stale must not
                # guess, or it clears the status of a running sync.
                logger.error(f"Startup sweep could not read sync leases: {e}")
                return
            if live:
                logger.info(
                    f"Startup sweep skipping {len(live)} connector(s) syncing elsewhere: "
                    f"{sorted(live)}"
                )
            stuck_keys -= live
            locked_only = {k: v for k, v in locked_only.items() if k not in live}
            if not stuck_keys and not locked_only:
                return

        now = get_epoch_timestamp_in_ms()
        payloads = [
            {
                "id": key,
                "status": AppStatus.IDLE.value,
                "isLocked": False,
                "updatedAtTimestamp": now,
                # The run this process was doing when it died never finished.
                ConnectorStateKeys.LAST_SYNC_FINISHED_AT: now,
                ConnectorStateKeys.LAST_SYNC_OUTCOME: SyncRunOutcome.FAILED.value,
                # The summary there is the run before it; left next to this
                # outcome, the two read as one run.
                ConnectorStateKeys.LAST_SYNC_SUMMARY: None,
                **(
                    {
                        ConnectorStateKeys.PENDING_FULL_SYNC: True,
                        ConnectorStateKeys.PENDING_RESYNC: True,
                    }
                    if key in full_sync_keys
                    else {}
                ),
            }
            for key in stuck_keys
        ]
        for key, status in locked_only.items():
            payload = {"id": key, "isLocked": False, "updatedAtTimestamp": now}
            if status != "DELETING":
                payload["status"] = AppStatus.IDLE.value
            payloads.append(payload)

        await graph_provider.batch_upsert_nodes(payloads, CollectionNames.APPS.value)
        reset_keys = sorted(stuck_keys | set(locked_only))
        logger.warning(
            f"Startup sweep reset stale sync state on {len(reset_keys)} connector(s): {reset_keys}"
        )
        owed = sorted(stuck_keys & full_sync_keys)
        if owed:
            logger.warning(
                f"Startup sweep: full sync did not finish on {len(owed)} connector(s); "
                f"they resume in full: {owed}"
            )
    except Exception as e:
        logger.error(f"Startup sweep for stale sync state failed: {e}", exc_info=True)


async def resume_sync_services(app_container: ConnectorAppContainer, data_store: GraphDataStore = None) -> bool:
    """Resume sync services for users with active sync states"""
    logger = app_container.logger()
    logger.debug("🔄 Checking for sync services to resume")

    try:
        graph_provider = data_store.graph_provider

        # Get all organizations
        orgs = await graph_provider.get_all_orgs(active=True)
        if not orgs:
            logger.info("No organizations found in the system")
            return True

        logger.info("Found %d organizations in the system", len(orgs))

        # Process each organization
        for org in orgs:
            org_id = org.get("_key") or org.get("id")
            accountType = org.get("accountType", AccountType.INDIVIDUAL.value)
            enabled_apps = await graph_provider.get_org_apps(org_id)
            app_names = [app["type"].replace(" ", "").lower() for app in enabled_apps]
            logger.info(f"App names: {app_names}")

            logger.info(
                "Processing organization %s with account type %s", org_id, accountType
            )

            # Get users for this organization
            users = await graph_provider.get_users(org_id, active=True)
            logger.info(f"User: {users}")
            if not users:
                logger.info("No users found for organization %s", org_id)
                continue

            logger.info("Found %d users for organization %s", len(users), org_id)

            config_service, org_data_store = await scope_org_resources(
                app_container, data_store, org_id
            )
            # Use pre-resolved data_store passed from lifespan to avoid coroutine reuse
            # data_store_provider = data_store if data_store else await app_container.data_store()

            # Initialize connectors_map if not already initialized
            if not hasattr(app_container, 'connectors_map'):
                app_container.connectors_map = {}

            # Initialize all enabled connectors in parallel so one slow
            # connector (e.g. a provider whose OAuth endpoint is slow) does
            # not gate the others on startup.
            # When this process is not the one that runs syncs, it still
            # builds the connector (routes need a warm one -- see
            # _ensure_connector_initialized, which otherwise pays a full init
            # plus a live connection test on the first record stream after every
            # restart) and publishes a resync instead of starting one.
            external = sync_executor_enabled()

            async def _init_app(app: dict) -> tuple[str, str, object | None]:
                connector_id = app.get("_key")
                scope = app.get("scope", "personal")
                created_by = app.get("createdBy", "")
                connector_name = app["type"].lower().replace(" ", "")
                # Only the event path runs a full sync and clears the flag; started
                # here, a connector owed one (queued at capacity before a restart)
                # would sync incrementally and stay flagged.
                publish = external or bool(app.get(ConnectorStateKeys.PENDING_FULL_SYNC))
                # Same lock the lazy-init paths use, and publish as soon as the
                # instance exists rather than after the whole gather: startup can
                # take seconds, and a request arriving in that window used to build
                # a second instance with its own HTTP client and rate limiter.
                async with connector_init_lock(connector_id):
                    if app_container.connectors_map.get(connector_id) is not None:
                        return connector_name, connector_id, None
                    try:
                        connector = await ConnectorFactory.create_and_start_sync(
                            name=connector_name,
                            logger=logger,
                            data_store_provider=org_data_store,
                            config_service=config_service,
                            connector_id=connector_id,
                            scope=scope,
                            created_by=created_by,
                            org_id=org_id,
                            data_entities_processor_cls=get_data_entities_processor_cls(),
                            notification_service=app_container.connector_notification_service(),
                            start_sync=not publish,
                        )
                    except Exception as e:
                        logger.error(
                            f"❌ Failed to initialize {connector_name} ({connector_id}) for org {org_id}: {e}",
                            exc_info=True,
                        )
                        return connector_name, connector_id, None
                    if connector:
                        app_container.connectors_map[connector_id] = connector

                # Outside the init lock: publishing goes to the broker, and the
                # lock only needs to cover instance construction.
                if publish and connector:
                    await _publish_startup_resync(
                        app_container,
                        config_service,
                        logger,
                        connector_name=connector_name,
                        connector_id=connector_id,
                        org_id=org_id,
                        app=app,
                        graph_provider=graph_provider,
                    )
                return connector_name, connector_id, connector

            results = await asyncio.gather(
                *[_init_app(app) for app in enabled_apps],
                return_exceptions=False,
            )
            for connector_name, connector_id, connector in results:
                if connector:
                    # _init_app already published it under the lock; this loop only reports.
                    logger.info(f"{connector_name} connector (id: {connector_id}) initialized for org %s", org_id)

            logger.info("✅ Sync services resumed for org %s", org_id)
        logger.info("✅ Sync services resumed for all orgs")
        return True
    except Exception as e:
        logger.error("❌ Error during sync service resumption: %s", str(e))
        logger.error("❌ Detailed error traceback:\n%s", traceback.format_exc())
        return False


async def _current_app(
    graph_provider: IGraphDBProvider | None, logger: logging.Logger, connector_id: str, snapshot: dict | None
) -> dict | None:
    """The app doc as it is now, not as the boot traversal read it.

    That read came before the connector's init (OAuth, a live connection test),
    which can take seconds while the API already serves requests, and a stop in
    that window clears pendingResync in the database, not in the snapshot.

    If the read fails, the snapshot is used. Not resuming would leave the marks
    set with nothing published until the next boot. Publishing is safe: the
    event is marked bootResume, and the start handler reads the doc again
    before it queues or deletes anything (``event_service._handle_start_sync``).
    """
    if graph_provider is None:
        return snapshot
    try:
        # raise_on_error: by default a failed read answers None, which reads as
        # "no such connector" and would skip a resume that is owed. None from
        # here means the doc is gone, and nothing is resumed.
        return await graph_provider.get_document(
            connector_id, CollectionNames.APPS.value, raise_on_error=True
        )
    except Exception as e:
        logger.warning(
            f"Could not re-read {connector_id} before resuming it; publishing the "
            f"resume anyway, the start handler checks it again: {e}"
        )
        return snapshot


async def _publish_startup_resync(
    app_container: ConnectorAppContainer,
    config_service,
    logger: logging.Logger,
    *,
    connector_name: str,
    connector_id: str,
    org_id: str,
    app: dict | None = None,
    graph_provider: IGraphDBProvider | None = None,
) -> None:
    """Hand a startup sync to whichever process consumes sync events.

    Respects the MANUAL strategy, which the event handler deliberately does not:
    that path also carries the user's resync button, and MANUAL means "not on a
    schedule", not "never". The one exception is a full sync a restart cut
    short, which resumes (``resumes_interrupted_full_sync``).

    Publishing rather than sharding by hash keeps this a single pass with no
    shard-count to get wrong, and the consumer's capacity gate meters the
    arrivals so a large fleet does not stampede one of them.
    """
    try:
        manual = await ConnectorFactory.is_manual_sync_strategy(config_service, connector_id)
        if manual:
            if not resumes_interrupted_full_sync(app) or not resumes_interrupted_full_sync(
                await _current_app(graph_provider, logger, connector_id, app)
            ):
                logger.info(
                    f"Not resuming {connector_name} {connector_id}: sync strategy is MANUAL"
                )
                return
            logger.info(
                f"Resuming {connector_name} {connector_id}: sync strategy is MANUAL, "
                "but a restart cut its full sync short"
            )
        dispatcher = get_dispatcher()
        if dispatcher is None:
            logger.warning(
                f"No sync dispatcher configured; cannot resume {connector_id}"
            )
            return
        result = await dispatcher.submit(
            SyncSpec(
                connector_id=connector_id,
                connector_name=connector_name,
                org_id=org_id,
                boot_resume=manual,
            )
        )
        logger.info(f"Startup resync for {connector_id}: {result.value}")
    except Exception as e:
        logger.error(f"Failed to publish startup resync for {connector_id}: {e}")


async def initialize_connector_registry(app_container: ConnectorAppContainer):
    """Initialize and sync connector registry with database"""
    logger = app_container.logger()
    logger.info("🔧 Initializing Connector Registry...")

    try:
        registry = get_connector_registry_cls()(app_container)

        register_extra_connectors()
        ConnectorFactory.initialize_beta_connector_registry()
        # Register connectors using generic factory
        available_connectors = ConnectorFactory.list_connectors()
        for connector_class in available_connectors.values():
            registry.register_connector(connector_class)
        logger.info("✅ Connectors registered")
        logger.info(f"Registered {len(registry._connectors)} connectors")

        # Sync with database
        await registry.sync_with_database()
        logger.info("✅ Connector registry synchronized with database")

        return registry

    except Exception as e:
        logger.error(f"❌ Error initializing connector registry: {str(e)}")
        raise

async def start_messaging_producer(app_container: ConnectorAppContainer) -> None:
    """Start messaging producer and attach it to container"""
    logger = app_container.logger()

    try:
        broker_type = get_message_broker_type()
        logger.info(f"🚀 Starting Messaging Producer (broker: {broker_type})...")

        producer_config = await MessagingUtils.create_producer_config(app_container)

        messaging_producer = MessagingFactory.create_producer(
            broker_type=broker_type,
            logger=logger,
            config=producer_config
        )
        await messaging_producer.initialize()

        app_container.messaging_producer = messaging_producer

        # Built here because it needs the producer; used by /sync/stop and by
        # anything that triggers a sync from inside this process.
        set_dispatcher(build_sync_dispatcher(logger, messaging_producer))

        # Wire the producer into KafkaService for connector operations
        kafka_service = app_container.kafka_service()
        kafka_service.set_producer(messaging_producer)

        logger.info("✅ Messaging producer started and attached to container")

    except Exception as e:
        logger.error(f"❌ Error starting messaging producer: {str(e)}")
        raise

# Consumers every connector depends on; health fails without them. The code
# graph's consumer and reconciler are optional: without them connectors still
# sync, so health reports them as degraded rather than failing the service.
_REQUIRED_CONSUMERS = frozenset({"entity", "sync"})
_RETRY_FIRST_DELAY_SECONDS = 5.0
_RETRY_MAX_DELAY_SECONDS = 300.0

# What /health reports. Started consumers are read live through is_running();
# components still retrying or that failed outright are recorded here.
_started_consumers: list = []
_component_status: dict[str, dict] = {}
_retry_tasks: list[asyncio.Task] = []


async def _keep_trying(name: str, attempt, logger: logging.Logger, first_error: Exception) -> None:  # noqa: ANN001
    """Retry an optional component's start with backoff until it comes up.

    A broker or Redis hiccup at boot used to leave the code graph without its
    consumer (or reconciler) until the next restart, with nothing saying so.
    """
    attempts, delay = 1, _RETRY_FIRST_DELAY_SECONDS
    while True:
        await asyncio.sleep(delay)
        attempts += 1
        try:
            await attempt()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _component_status[name] = {"state": "retrying", "attempts": attempts, "error": str(e)}
            logger.error(f"❌ {name} failed to start (attempt {attempts}); retrying in "
                         f"{min(delay * 2, _RETRY_MAX_DELAY_SECONDS):.0f}s: {e}")
            delay = min(delay * 2, _RETRY_MAX_DELAY_SECONDS)
            continue
        _component_status.pop(name, None)
        logger.info(f"✅ {name} started after {attempts} attempts")
        return


def _start_retrying(name: str, attempt, logger: logging.Logger, error: Exception) -> None:  # noqa: ANN001
    # Set before the task first runs, so health never misses the failure.
    _component_status[name] = {"state": "retrying", "attempts": 1, "error": str(error)}
    task = asyncio.create_task(_keep_trying(name, attempt, logger, error), name=f"retry_{name}")
    _retry_tasks.append(task)


def component_health() -> dict[str, dict]:
    """Each consumer and optional component, with whether health depends on it."""
    report: dict[str, dict] = {}
    for name, consumer in _started_consumers:
        try:
            running = bool(consumer.is_running())
        except Exception:
            running = False
        report[name] = {"state": "running" if running else "stopped",
                        "required": name in _REQUIRED_CONSUMERS}
    for name, component in _component_status.items():
        report[name] = {**component, "required": name in _REQUIRED_CONSUMERS}
    return report


async def start_kafka_consumers(app_container: ConnectorAppContainer, graph_provider) -> list:
    """Start all message consumers at application level"""
    logger = app_container.logger()
    # The registry /health reads, so a consumer the retry starts later shows up.
    consumers = _started_consumers
    consumers.clear()
    _component_status.clear()
    broker_type = get_message_broker_type()

    try:
        # Create RetryManager for persistent failure retry tracking
        redis_config = await MessagingUtils._get_redis_config(app_container)
        retry_manager = MessagingFactory.create_retry_manager(logger, redis_config)
        await retry_manager.initialize()
        logger.info("✅ RetryManager initialized for %s consumers", broker_type.value)

        # 1. Create Entity Consumer
        logger.info(f"🚀 Starting Entity Consumer (broker: {broker_type})...")
        entity_config = await MessagingUtils.create_entity_consumer_config(app_container)
        entity_consumer = MessagingFactory.create_consumer(
            broker_type=broker_type,
            logger=logger,
            config=entity_config,
            retry_manager=retry_manager
        )
        entity_message_handler = await KafkaUtils.create_entity_message_handler(app_container, graph_provider)
        await entity_consumer.start(entity_message_handler)
        consumers.append(("entity", entity_consumer))
        logger.info("✅ Entity consumer started")

        # 2. Create Sync Consumer — unless another process owns sync execution, in
        # which case a consumer here would race them for every event.
        if sync_executor_enabled():
            logger.info(
                "⏭️  Sync consumer not started: CONNECTOR_SYNC_MODE=external, "
                "sync events are consumed by another process"
            )
            logger.info(f"✅ All {len(consumers)} consumers started successfully")
            return consumers

        logger.info(f"🚀 Starting Sync Consumer (broker: {broker_type})...")
        # uvicorn forks N workers sharing one HOSTNAME, so the default
        # consumer name is identical in all of them. Redis Streams keys the
        # pending-entries list by that name, so every worker would re-read its
        # other processes' in-flight sync events on boot. Left alone for a single worker,
        # where the name is already unique and stable across restarts.
        sync_client_id = (
            process_identity() if max_connector_workers() > 1 else executor_identity()
        )
        sync_config = await MessagingUtils.create_sync_consumer_config(
            app_container, client_id=sync_client_id
        )
        sync_consumer = MessagingFactory.create_consumer(
            broker_type=broker_type,
            logger=logger,
            config=sync_config,
            retry_manager=retry_manager
        )
        sync_message_handler = await KafkaUtils.create_sync_message_handler(app_container, graph_provider)
        await sync_consumer.start(sync_message_handler)
        consumers.append(("sync", sync_consumer))
        logger.info("✅ Sync consumer started")

        # 3. Code Graph Consumer. Optional: its failure must not stop the
        # entity and sync consumers already running, and it keeps retrying.
        async def start_code_graph_consumer() -> None:
            logger.info(f"🚀 Starting Code Graph Consumer (broker: {broker_type})...")
            code_graph_config = await MessagingUtils.create_code_graph_consumer_config(
                app_container
            )
            code_graph_consumer = MessagingFactory.create_consumer(
                broker_type=broker_type,
                logger=logger,
                config=code_graph_config,
                retry_manager=retry_manager,
            )
            code_graph_message_handler = await KafkaUtils.create_code_graph_message_handler(
                app_container,
                graph_provider,
            )
            await code_graph_consumer.start(code_graph_message_handler)
            # The same list the container holds, so a late start is still
            # stopped at shutdown and shows in health.
            consumers.append(("code_graph", code_graph_consumer))
            logger.info("✅ Code graph consumer started")

        try:
            await start_code_graph_consumer()
        except Exception as e:
            logger.error(f"❌ Code graph consumer failed to start: {e}", exc_info=True)
            _start_retrying("code_graph", start_code_graph_consumer, logger, e)

        logger.info(f"✅ All {len(consumers)} consumers started successfully")
        return consumers

    except Exception as e:
        _component_status["message_consumers"] = {"state": "failed", "error": str(e)}
        logger.error(f"❌ Error starting consumers: {str(e)}")
        for name, consumer in consumers:
            try:
                await consumer.stop()
                logger.info(f"Stopped {name} consumer during cleanup")
            except Exception as cleanup_error:
                logger.error(f"Error stopping {name} consumer during cleanup: {cleanup_error}")
        raise

async def stop_kafka_consumers(container: ConnectorAppContainer) -> None:
    """Stop all Kafka consumers"""

    logger = container.logger()
    for task in _retry_tasks:
        task.cancel()
    _retry_tasks.clear()
    consumers = getattr(container, 'kafka_consumers', [])
    for name, consumer in consumers:
        try:
            await consumer.stop()
            logger.info(f"✅ {name.title()} message consumer stopped")
        except Exception as e:
            logger.error(f"❌ Error stopping {name} consumer: {str(e)}")

    # Clear the consumers list
    if hasattr(container, 'kafka_consumers'):
        container.kafka_consumers = []

async def stop_messaging_producer(container: ConnectorAppContainer) -> None:
    """Stop the messaging producer"""
    logger = container.logger()

    try:
        # Get the messaging producer from container
        messaging_producer = getattr(container, 'messaging_producer', None)
        if messaging_producer:
            await messaging_producer.cleanup()
            logger.info("✅ Messaging producer stopped successfully")
        else:
            logger.info("No messaging producer to stop")
    except Exception as e:
        logger.error(f"❌ Error stopping messaging producer: {str(e)}")

async def shutdown_container_resources(container: ConnectorAppContainer) -> None:
    """Shutdown all container resources properly"""
    logger = container.logger()

    try:
        try:
            await stop_sync_reaper(getattr(container, "sync_reaper_task", None))
        except Exception as e:
            logger.warning(f"Error stopping sync reaper at shutdown: {e}")

        # Consumers first. An event admitted during the cancel window takes a
        # lease that coordinator.stop() then leaves un-renewed and un-signalled,
        # so the sync runs on blind and the connector is pinned SYNCING until the
        # TTL lapses. Stopping consumers first is what avoids that window.
        await stop_kafka_consumers(container)

        # Cancel all running connector sync/reindex tasks so they can clean up
        # gracefully before the database and messaging connections are torn down
        try:
            coordinator = get_coordinator()
            if coordinator is not None:
                await coordinator.cancel_all()
        except Exception as e:
            logger.warning(f"Error cancelling sync tasks at shutdown: {e}")

        try:
            await reindex_task_manager.cancel_all()
        except Exception as e:
            logger.warning(f"Error cancelling reindex tasks at shutdown: {e}")

        try:
            await code_edge_build_task_manager.cancel_all()
        except Exception as e:
            logger.warning(f"Error cancelling code edge build tasks at shutdown: {e}")

        # cancel_all only waits for the sync tasks; a finalizer detached by a
        # second cancel is still writing IDLE, releasing its lease and
        # publishing. Closing Redis and the producer under it is what leaves
        # connectors SYNCING with a held lease after a restart.
        try:
            outstanding = await join_cleanup_tasks(timeout=30.0)
            if outstanding:
                logger.info("Waited on %d detached sync finalizer(s)", outstanding)
        except Exception as e:
            logger.warning(f"Error waiting for sync finalizers at shutdown: {e}")

        try:
            coordinator = get_coordinator()
            if coordinator is not None:
                await coordinator.stop()
        except Exception as e:
            logger.warning(f"Error stopping sync lease manager at shutdown: {e}")

        code_edge_build_redis = vars(container).get("code_edge_build_redis")
        if code_edge_build_redis is not None:
            try:
                await code_edge_build_redis.aclose()
                container.code_edge_build_redis = None
            except Exception as e:
                logger.warning(f"Error closing code edge build Redis client: {e}")

        # Stop messaging producer
        await stop_messaging_producer(container)

        # Stop startup services (token refresh)
        try:
            await startup_service.shutdown()
        except Exception as e:
            logger.warning(f"Error shutting down startup services: {e}")

        # Close configuration service (stops Redis Pub/Sub subscription)
        try:
            config_service = container.config_service()
            await config_service.close()
        except Exception as e:
            logger.error(f"Error closing configuration service: {e}")

        # Last, and the only place the shared pool is ever shut down — connector
        # cleanup() only drains its own lease, since other connectors share this.
        thread_pool = getattr(container, "connector_thread_pool", None)
        if thread_pool is not None:
            try:
                thread_pool.shutdown(wait=False)
            except Exception as e:
                logger.warning(f"Error shutting down connector thread pool: {e}")

        logger.info("✅ All container resources shut down successfully")

    except Exception as e:
        logger.error(f"❌ Error during container resource shutdown: {str(e)}")

async def refresh_connector_metrics(graph_provider, logger, interval_s: int = 60) -> None:
    """Periodically refresh the connector_active gauge; best-effort, never fatal."""
    while True:
        try:
            # Counted in the database: a tenant can have thousands of collection
            # Apps, and reading every App here stalled the event loop for seconds.
            counts = await graph_provider.count_active_apps_by_type()
            set_connector_active(counts)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Failed to refresh connector_active gauge: {e}")
        await asyncio.sleep(interval_s)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for FastAPI"""
    mark_process_non_dumpable()
    install_log_redaction()
    # Initialize container
    app_container = await get_initialized_container()
    app.container = app_container  # type: ignore

    app.state.config_service = app_container.config_service()

    # Resolve data_store first - this internally resolves graph_provider
    data_store = await app_container.data_store()
    app.state.graph_provider = data_store.graph_provider

    # Initialize connector registry
    # Use the already-resolved graph_provider from data_store to avoid coroutine reuse
    logger = app_container.logger()
    graph_provider = data_store.graph_provider

    # Sync completion and KB deletes happen here; both drop the query service's
    # cached accessible-record maps.
    try:
        from app.services.cache.accessible_records_cache import AccessibleRecordsCache
        from app.services.cache.invalidation_hooks import (
            init_accessible_records_invalidator,
        )
        accessible_records_cache = await AccessibleRecordsCache.create(
            logger, app_container.config_service()
        )
        app.state.accessible_records_cache = accessible_records_cache
        init_accessible_records_invalidator(logger, accessible_records_cache, graph_provider)
    except Exception as e:
        logger.warning(f"❌ Failed to register accessible-records invalidator: {e}")

    try:
        await telemetry.bind(app_container.config_service(), logger).start()
    except Exception as e:
        logger.warning(f"❌ Failed to start telemetry pusher: {e}")

    if telemetry_enabled():
        app.state.connector_metrics_task = asyncio.create_task(
            refresh_connector_metrics(graph_provider, logger, interval_s=60*5), name="connector_metrics_refresh"
        )

    # Every connector's blocking vendor-SDK calls run here, each through a capped
    # lease. Created before _post_startup so resume_sync_services and the event
    # service both find it already on the container.
    app_container.connector_thread_pool = get_shared_connector_thread_pool()
    logger.info(
        "✅ Shared connector thread pool ready (max_workers=%d)",
        app_container.connector_thread_pool.max_workers,
    )

    # Start token refresh service at app startup (database-agnostic)
    try:
        await startup_service.initialize(
            app_container.config_service(),
            graph_provider,
            **get_startup_extra_kwargs(app_container),
        )
        logger.info("✅ Startup services initialized successfully")
    except Exception as e:
        logger.warning(f"⚠️ Startup token refresh service failed to initialize: {e}")

    # Initialize connector registry - pass already-resolved graph_provider
    registry = await initialize_connector_registry(app_container)
    app.state.connector_registry = registry
    logger.info("✅ Connector registry initialized and synchronized with database")

    # Initialize toolset registry (in-memory, fast tool lookup)
    logger.info("🔄 Initializing in-memory toolset registry...")
    toolset_registry = get_toolset_registry()
    toolset_registry.auto_discover_toolsets()
    app.state.toolset_registry = toolset_registry
    logger.info(f"✅ Loaded {len(toolset_registry.list_toolsets())} toolsets in memory")

    # Initialize MCP server catalog registry (in-memory, mirrors the toolset registry)
    logger.info("🔄 Initializing in-memory MCP server registry...")
    mcp_registry = get_mcp_registry()
    mcp_registry.auto_discover_templates()
    app.state.mcp_registry = mcp_registry
    logger.info(f"✅ Loaded {len(mcp_registry.list_templates())} MCP server templates in memory")

    # Initialize OAuth config registry (completely independent, no connector registry needed)
    # Note: OAuth registry is populated when connectors are registered above
    oauth_registry = get_oauth_config_registry()
    app.state.oauth_config_registry = oauth_registry
    logger.info("✅ OAuth config registry initialized")

    # Needs the connectors' OAuth registrations, so it runs after the registry is built.
    try:
        from app.migrations.connector_oauth_toolset_fields_migration import (
            run_connector_oauth_toolset_fields_repair,
        )

        repair_result = await run_connector_oauth_toolset_fields_repair(
            app_container.config_service(), oauth_registry, logger
        )
        if not repair_result.get("skipped"):
            logger.info(
                "Connector OAuth app repair: %s app(s) repaired, %s failure(s)",
                repair_result.get("apps_repaired", 0),
                repair_result.get("failures", 0),
            )
    except Exception as e:
        logger.error(f"❌ Connector OAuth app repair failed: {e}", exc_info=True)

    # Before syncs resume: S3 and MinIO no longer read the created filter, so a saved one must move first.
    try:
        from app.migrations.s3_created_date_filter_migration import (
            run_s3_created_date_filter_migration,
        )

        await run_s3_created_date_filter_migration(
            app_container.config_service(), graph_provider, logger
        )
    except Exception as e:
        logger.error(f"❌ S3 created date filter migration failed: {e}", exc_info=True)

    # Before syncs resume: earlier Nextcloud syncs linked whoever had the Nextcloud profile email to the app.
    try:
        from app.migrations.nextcloud_user_app_edges_migration import (
            run_nextcloud_user_app_edges_migration,
        )

        await run_nextcloud_user_app_edges_migration(graph_provider, app_container.config_service(), logger)
    except Exception as e:
        logger.error(f"❌ Nextcloud user-app edges migration failed: {e}", exc_info=True)

    # Before any connector reads its settings, so none crawls with a value the form doesn't show.
    try:
        from app.migrations.web_browser_identity_migration import (
            run_web_browser_identity_backfill,
        )

        backfill_result = await run_web_browser_identity_backfill(app_container, graph_provider, logger)
        if not backfill_result.get("skipped"):
            logger.info(
                "Web connector identity backfill: %s connector(s) updated, %s failure(s)",
                backfill_result.get("connectors_updated", 0),
                backfill_result.get("failures", 0),
            )
    except Exception as e:
        logger.error(f"❌ Web connector identity backfill failed: {e}", exc_info=True)

    logger.debug("🚀 Starting application")

    # Start messaging producer first
    try:
        await start_messaging_producer(app_container)
        startup_service.set_messaging_producer(app_container.messaging_producer)
        logger.info("✅ Messaging producer started successfully")
    except Exception as e:
        logger.error(f"❌ Failed to start messaging producer: {str(e)}")
        raise

    # Resume sync services and start Kafka consumers off the startup critical
    # path. Both steps make blocking calls (per-connector init, Kafka group
    # join) that can take tens of seconds; running them here would keep the
    # HTTP server from binding and fail the container-level /health probe.
    # We run them in a single background task to preserve the ordering
    # contract: connectors_map must be populated before the sync consumer
    # begins processing events.
    async def _post_startup() -> None:
        # Before anything can spawn a sync, and before the sweep — which needs
        # leases to tell a live sync from a stale status.
        try:
            await initialize_sync_coordinator(app_container, logger)
        except Exception as e:
            # Without a coordinator nothing below can run safely, consumers
            # included. This is a background task, so raising only hid it: the
            # service kept answering /health 200 while consuming nothing.
            logger.critical(f"❌ Sync coordinator init failed: {e}", exc_info=True)
            # /health shows this to callers; the cause is in the log line above.
            app.state.startup_error = "sync coordinator init failed"
            return

        # A resumed sync writes SYNCING as its first act, and this sweep must
        # not clobber that write.
        try:
            await reset_stale_sync_state(graph_provider, logger)
        except Exception as e:
            logger.error(f"❌ Stale sync-state sweep failed: {e}", exc_info=True)

        # Refreshes OAuth tokens, so it has to land before connector init() runs
        # against credentials that expired during downtime.
        try:
            await pre_sync_hook(app_container, logger)
        except Exception as e:
            logger.error(f"❌ pre_sync_hook failed: {str(e)}", exc_info=True)

        try:
            await resume_sync_services(app_container, data_store)
        except Exception as e:
            logger.error(f"❌ Error during sync service resumption: {str(e)}")

        try:
            consumers = await start_kafka_consumers(app_container, graph_provider)
            app_container.kafka_consumers = consumers
            logger.info("✅ All message consumers started successfully")

            coordinator = get_coordinator()
            if coordinator is not None:
                app_container.sync_reaper_task = start_sync_reaper(
                    graph_provider, coordinator, logger
                )
        except Exception as e:
            logger.error(f"❌ Failed to start message consumers: {str(e)}", exc_info=True)

        # Anything parked at the concurrency limit when the process went down
        # has no finishing sync to release it, so it would sit QUEUED forever.
        try:
            await drain_queued_syncs(graph_provider, logger)
        except Exception as e:
            logger.error(f"Could not release queued syncs at startup: {e}")

        # After the consumers, so the reconciler shares the event service (and
        # its lock client and task manager) the code-graph consumer created.
        async def start_reconciler() -> None:
            code_graph_event_service = await KafkaUtils.create_code_graph_event_service(
                app_container, graph_provider
            )
            app.state.code_edge_build_reconcile_task = asyncio.create_task(
                reconcile_forever(graph_provider, code_graph_event_service, logger),
                name="code_edge_build_reconcile",
            )
            logger.info("✅ Code edge build reconciler started")

        try:
            await start_reconciler()
        except Exception as e:
            logger.error(f"❌ Failed to start code edge build reconciler: {str(e)}", exc_info=True)
            _start_retrying("code_edge_reconciler", start_reconciler, logger, e)

        # Last: a large install takes a while, and lookups fall back meanwhile.
        try:
            from app.migrations.code_block_name_folded_migration import (
                run_code_block_name_folded_backfill,
            )

            await run_code_block_name_folded_backfill(
                app_container.config_service(), graph_provider, logger
            )
        except Exception as e:
            logger.error(f"❌ Code block nameFolded backfill failed: {e}", exc_info=True)

        # After nameFolded: re-indexes repos whose blocks predate this code
        # graph version, once per version.
        try:
            from app.migrations.code_graph_backfill import run_code_graph_backfill

            await run_code_graph_backfill(
                app_container.config_service(), graph_provider, app_container.kafka_service(), logger,
            )
        except Exception as e:
            logger.error(f"❌ Code graph backfill failed: {e}", exc_info=True)

    post_startup_task = asyncio.create_task(_post_startup(), name="connector_post_startup")
    app_container.post_startup_task = post_startup_task

    # Needs the producer started above; a Redis lease keeps it to one replica.
    from app.connectors.services.trash_purge import run_trash_purge_loop
    app.state.trash_purge_task = asyncio.create_task(
        run_trash_purge_loop(app_container, graph_provider), name="trash_purge"
    )
    # Retries the storage release of connector/KB deletes that did not finish it.
    from app.connectors.services.storage_release import run_storage_release_loop
    async def _org_config_service(org_id: str) -> ConfigurationService:
        # The store connectors read their credentials from, so a release deletes them there.
        config_service, _ = await scope_org_resources(app_container, data_store, org_id)
        return config_service

    app.state.storage_release_task = asyncio.create_task(
        run_storage_release_loop(app_container, graph_provider, config_service_for=_org_config_service),
        name="storage_release",
    )

    # Here, not in indexing: it publishes re-index events on this service's producer.
    from app.connectors.services.stored_content_heal import run_stored_content_heal_loop
    app.state.stored_content_heal_task = asyncio.create_task(
        run_stored_content_heal_loop(app_container, graph_provider), name="stored_content_heal"
    )
    # Moves pre-hierarchical records/<vrid> copies into the hierarchy; one replica leads.
    from app.connectors.services.flat_storage_migration import (
        run_flat_storage_migration,
    )

    app.state.flat_storage_migration_task = asyncio.create_task(
        run_flat_storage_migration(app_container, graph_provider), name="flat_storage_migration",
    )

    # NOTE: ToolsetTokenRefreshService.start() already performs an initial refresh scan.
    # Avoid triggering another startup scan here to prevent duplicate scheduling attempts.

    yield

    # Ensure background startup work has settled before tearing things down.
    if not post_startup_task.done():
        post_startup_task.cancel()
        try:
            await post_startup_task
        except (asyncio.CancelledError, Exception):
            pass
    logger.info("🔄 Shut down application started")
    # The code-edge reconciler stops before the task managers drain, so its
    # sweep cannot start a build into the unwind.
    for task_name in (
        "connector_metrics_task", "trash_purge_task", "storage_release_task",
        "stored_content_heal_task", "code_edge_build_reconcile_task",
        "flat_storage_migration_task",
    ):
        task = getattr(app.state, task_name, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
    if telemetry.pusher is not None:
        await telemetry.pusher.stop()
    try:
        accessible_records_cache = getattr(app.state, "accessible_records_cache", None)
        if accessible_records_cache is not None:
            await accessible_records_cache.close()
            logger.info("✅ Accessible-records cache closed")
    except Exception as e:
        logger.error(f"❌ Error closing accessible-records cache: {e}")
    # Shutdown all container resources
    try:
        await shutdown_container_resources(app_container)
    except Exception as e:
        logger.error(f"❌ Error during application shutdown: {str(e)}")


# Create FastAPI app with lifespan
_app_dependencies = [Depends(get_initialized_container)]
if ensure_org_context is not None:
    _app_dependencies.append(Depends(ensure_org_context))

app = FastAPI(
    title="Connectors Sync Service",
    description="Service for syncing content from connectors to GraphDB",
    version="1.0.0",
    lifespan=lifespan,
    dependencies=_app_dependencies,
)
configure_app(app, "connectors")

# List of paths to exclude from authentication (public endpoints)
# All other paths will require authentication by default
EXCLUDE_PATHS = [
    "/health",          # Basic health check endpoint
    "/health/graph-db", # Graph DB connectivity probe (called by Node.js health route)
    "/health/vector-db", # Vector DB connectivity probe (called by Node.js health route)
    "/drive/webhook",   # Google Drive webhook (has its own WebhookAuthVerifier)
    "/gmail/webhook",   # Gmail webhook (uses Google Pub/Sub authentication)
    "/admin/webhook",   # Admin webhook (has its own WebhookAuthVerifier)
]

@app.middleware("http")
async def authenticate_requests(request: Request, call_next) -> JSONResponse:
    """
    Authentication middleware that authenticates all requests by default,
    except for paths explicitly excluded (webhooks, health checks, OAuth callbacks).
    """
    logger = app.container.logger()  # type: ignore
    request_path = request.url.path
    logger.debug(f"Middleware processing request: {request_path}")

    # Check if path should be excluded from authentication
    should_exclude = False

    # Check exact path matches for webhooks and health
    if request_path in EXCLUDE_PATHS:
        should_exclude = True
        logger.debug(f"Excluding exact path match: {request_path}")

    # Check for OAuth callback paths (pattern-based exclusion)
    # if "/oauth/callback" in request_path:
    #     should_exclude = True
    #     logger.debug(f"Excluding OAuth callback path: {request_path}")



    # If path should be excluded, skip authentication
    if should_exclude:
        logger.debug(f"Skipping authentication for excluded path: {request_path}")
        return await call_next(request)

    # All other paths require authentication
    try:
        logger.debug(f"Applying authentication for path: {request_path}")
        authenticated_request = await authMiddleware(request)
        return await call_next(authenticated_request)

    except HTTPException as exc:
        # Handle authentication errors
        logger.warning(f"Authentication failed for {request_path}: {exc.detail}")
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    except Exception as e:
        # Handle unexpected errors
        logger.error(f"Unexpected error during authentication for {request_path}: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )


# Trace context — outermost, before auth.
app.add_middleware(RequestContextMiddleware)
# Telemetry: outermost metrics middleware; pusher started/stopped in lifespan.
telemetry = setup_telemetry(app, service_name="connector_service")


@app.get("/health")
async def health_check() -> JSONResponse:
    """Basic health check endpoint"""
    startup_error = getattr(app.state, "startup_error", None)
    if startup_error:
        return JSONResponse(
            status_code=503,
            content={
                "status": "fail",
                "error": startup_error,
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )
    try:
        components = component_health()
        down = [name for name, c in components.items() if c["state"] != "running"]
        required_down = [name for name in down if components[name]["required"]
                         or name == "message_consumers"]
        overall = "fail" if required_down else ("degraded" if down else "healthy")
        return JSONResponse(
            # Degraded stays 200: an optional component missing is not a reason
            # for an orchestrator to restart a service that is still syncing.
            status_code=503 if required_down else 200,
            content={
                "status": overall,
                "components": components,
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )
    except Exception:
        logging.getLogger(__name__).error("Health check failed", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "fail",
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )


@app.get("/health/graph-db")
async def graph_db_health_check(request: Request) -> JSONResponse:
    """Probe the configured graph database using the same driver the app uses.

    Handles both ArangoDB and Neo4j so that the Node.js health endpoint can
    delegate the graph-DB check here for both database types instead of
    performing its own probes (which fail for managed deployments like Neo4j
    Aura that don't expose HTTP discovery ports 7474/7473).
    """
    data_store = os.getenv("DATA_STORE", "arangodb").lower()

    # ── ArangoDB ──────────────────────────────────────────────────────────────
    if data_store == "arangodb":
        try:
            container = request.app.container  # type: ignore[attr-defined]
            config_service = container.config_service()
            arangodb_config = await config_service.get_config(
                config_node_constants.ARANGODB.value
            )
            username = arangodb_config["username"]
            password = arangodb_config["password"]

            client = await container.arango_client()
            # python-arango is synchronous; offload so the event loop stays free.
            sys_db = await asyncio.to_thread(
                client.db, "_system", username=username, password=password
            )
            await asyncio.to_thread(sys_db.version)

            return JSONResponse(
                status_code=200,
                content={"status": "healthy", "timestamp": get_epoch_timestamp_in_ms()},
            )
        except Exception:
            request.app.container.logger().error("ArangoDB health check failed", exc_info=True)
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unhealthy",
                    "error": "ArangoDB health check failed",
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )

    # ── Neo4j ─────────────────────────────────────────────────────────────────
    # One RETURN 1 on the driver already open. execute_query can connect and
    # rebuild that driver; a 3s wait_for cancelled mid-handshake would leave
    # the new driver installed. A fresh Bolt handshake can also take longer
    # than Node's 5s budget while Neo4j is still up.
    if data_store == "neo4j":
        from neo4j.exceptions import AuthError, ServiceUnavailable

        # Under Node's 5s graph timeout, which is also the Helm probe budget.
        # Acquisition is shorter than the wait so an idle-connection RESET
        # fails inside the driver and releases the pool slot. wait_for
        # cancelling that RESET would leave the slot checked out.
        neo4j_probe_timeout_sec = 3.0
        neo4j_acquire_timeout_sec = 2.0

        try:
            graph_provider = getattr(request.app.state, "graph_provider", None)
            if graph_provider is None:
                container = request.app.container  # type: ignore[attr-defined]
                graph_provider = await container.graph_provider()

            client = getattr(graph_provider, "client", None)
            driver = getattr(client, "driver", None) if client is not None else None
            if driver is None:
                request.app.container.logger().error(  # type: ignore[attr-defined]
                    "Neo4j health check failed: provider has no driver"
                )
                return JSONResponse(
                    status_code=503,
                    content={
                        "status": "unhealthy",
                        "error": "Neo4j unavailable",
                        "timestamp": get_epoch_timestamp_in_ms(),
                    },
                )

            database = getattr(client, "database", None)

            async def _ping() -> None:
                async with driver.session(
                    database=database,
                    connection_acquisition_timeout=neo4j_acquire_timeout_sec,
                ) as session:
                    result = await session.run("RETURN 1 AS n")
                    await result.consume()

            await asyncio.wait_for(_ping(), timeout=neo4j_probe_timeout_sec)
            return JSONResponse(
                status_code=200,
                content={"status": "healthy", "timestamp": get_epoch_timestamp_in_ms()},
            )
        except TimeoutError:
            request.app.container.logger().error(  # type: ignore[attr-defined]
                "Neo4j health check timed out after %.1fs", neo4j_probe_timeout_sec
            )
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unhealthy",
                    "error": "Neo4j health check timed out",
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )
        except AuthError as e:
            request.app.container.logger().error("Neo4j auth failed: %s", e)
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unhealthy",
                    "error": "Neo4j auth failed",
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )
        except ServiceUnavailable as e:
            request.app.container.logger().error("Neo4j unavailable: %s", e)
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unhealthy",
                    "error": "Neo4j unavailable",
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )
        except Exception:
            request.app.container.logger().error("Neo4j health check failed", exc_info=True)
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unhealthy",
                    "error": "Neo4j health check failed",
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )

    # Unknown DATA_STORE value — treat as healthy so we don't block deployments.
    return JSONResponse(
        status_code=200,
        content={
            "status": "healthy",
            "message": f"graph-db health check not implemented for DATA_STORE={data_store}",
            "timestamp": get_epoch_timestamp_in_ms(),
        },
    )


@app.get("/health/vector-db")
async def vector_db_health_check(request: Request) -> JSONResponse:
    """Probe the configured vector database.

    Uses VECTOR_DB_TYPE to determine which provider to check (qdrant, opensearch,
    or redis). Called by the Node.js health endpoint so it doesn't need to know
    which vector DB is deployed.

    The provider is created once and cached on app.state for subsequent calls.
    """
    from app.services.vector_db.models import HealthStatus
    from app.services.vector_db.vector_db_provider_factory import VectorDBProviderFactory
    from app.utils.logger import create_logger

    vector_db_type = os.getenv("VECTOR_DB_TYPE", "qdrant").lower().strip()

    try:
        # Reuse cached provider to avoid re-creating on every health check call
        provider = getattr(request.app.state, "_vector_db_health_provider", None)
        if provider is None:
            logger = create_logger("vector_db_health")
            container = request.app.container  # type: ignore[attr-defined]
            config_service = container.config_service()
            provider = await VectorDBProviderFactory.create_provider(
                logger=logger,
                config_service=config_service,
            )
            request.app.state._vector_db_health_provider = provider

        result = await provider.health_check()

        if result.status == HealthStatus.HEALTHY:
            return JSONResponse(
                status_code=200,
                content={
                    "status": "healthy",
                    "provider": vector_db_type,
                    "version": result.server_version,
                    "latency_ms": result.latency_ms,
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )
        else:
            request.app.container.logger().warning(
                "Vector DB (%s) reported unhealthy: %s", vector_db_type, result.message
            )
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unhealthy",
                    "provider": vector_db_type,
                    "error": f"{vector_db_type} health check failed",
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )
    except Exception:
        request.app.container.logger().error(
            "Vector DB (%s) health check failed", vector_db_type, exc_info=True
        )
        # Clear cached provider so next call retries connection
        request.app.state._vector_db_health_provider = None
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "provider": vector_db_type,
                "error": f"Vector DB ({vector_db_type}) health check failed",
                "timestamp": get_epoch_timestamp_in_ms(),
            },
        )


@app.get("/health/vector-db/memory")
async def vector_db_memory_check(request: Request) -> JSONResponse:
    """Whether the vector database has the memory to keep search fast.

    Advisory only: it never changes the health status. It reports the index's
    total size and Qdrant's memory (the host's RAM when no limit is set), so it
    is not in EXCLUDE_PATHS and it refuses non-admins itself: the Node.js route
    checks too, but this port can be reachable directly. Reuses the provider
    cached by ``/health/vector-db``.
    """
    from app.api.middlewares.auth import is_request_admin
    from app.services.vector_db.vector_db_provider_factory import (
        VectorDBProviderFactory,
    )
    from app.utils.logger import create_logger

    if not is_request_admin(request):
        return JSONResponse(status_code=403, content={"detail": "Admin access required"})

    try:
        provider = getattr(request.app.state, "_vector_db_health_provider", None)
        if provider is None:
            container = request.app.container  # type: ignore[attr-defined]
            provider = await VectorDBProviderFactory.create_provider(
                logger=create_logger("vector_db_health"),
                config_service=container.config_service(),
            )
            request.app.state._vector_db_health_provider = provider

        report = await provider.memory_report()
        if report is None:
            return JSONResponse(status_code=200, content={"status": "unsupported"})
        return JSONResponse(status_code=200, content=report.to_dict())
    except Exception:
        request.app.container.logger().warning(
            "Vector DB memory report failed", exc_info=True
        )
        return JSONResponse(
            status_code=503,
            content={"status": "unknown", "error": "Vector DB memory report failed"},
        )


# Include routes - more specific routes first
app.include_router(entity_router)
app.include_router(toolsets_router)
app.include_router(mcp_servers_router)
app.include_router(api_toolsets_router)
app.include_router(kb_router)
app.include_router(knowledge_hub_router)
app.include_router(demo_data_router)
app.include_router(connector_router)
app.include_router(artifacts_router)
if oauth_apps_router is not None:
    app.include_router(oauth_apps_router)
if sharing_router is not None:
    app.include_router(sharing_router)
if knowledge_hub_service_factory is not None:
    app.dependency_overrides[get_knowledge_hub_service] = knowledge_hub_service_factory



# Global error handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger = app.container.logger()  # type: ignore
    logger.error("Global error: %s", str(exc), exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "message": SOMETHING_WENT_WRONG,
            "path": request.url.path,
        },
    )


def run(host: str = "0.0.0.0", port: int = 8088, workers: int | None = None, reload: bool = True) -> None:
    """Run the application.

    ``workers`` comes from the edition seam: this build always answers 1,
    because running syncs in more than one process needs exclusion this build
    does not have -- its own guard is a per-process dict plus a fail-open
    database read.

    Where the lease is active, syncs are excluded process-wide. Reindex is not
    leased — its dedup (``reindex_task_manager``) is still an in-memory dict per
    process, so a duplicate reindex landing on a different worker than the
    in-flight one can still run concurrently.
    """
    if workers is None:
        try:
            workers = max_connector_workers()
        except ValueError:
            workers = 1
    if reload and workers > 1:
        workers = 1
    from app.utils.env_utils import uvicorn_worker_healthcheck_timeout

    uvicorn.run(
        "app.connectors_main:app",
        host=host,
        port=port,
        log_level="info",
        reload=reload,
        workers=workers,
        timeout_worker_healthcheck=uvicorn_worker_healthcheck_timeout(),
    )


if __name__ == "__main__":
    run(reload=False)
