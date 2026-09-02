"""Generic Event Service for handling connector-specific events"""

import asyncio
import logging
import os
import time
from collections import OrderedDict
from typing import Any

from dependency_injector import providers

from app.config.constants.arangodb import (
    AppStatus,
    CollectionNames,
    Connectors,
    EventTypes,
    ProgressStatus,
)
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.connector.instance_lock import connector_init_lock
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.core.constants import ConnectorStateKeys
from app.connectors.core.factory.connector_factory import ConnectorFactory
from app.connectors.core.sync.sync_dispatcher import SyncSpec
from app.connectors.core.sync.sync_coordinator import (
    Admission,
    SyncCoordinator,
    SyncLease,
    _safe_limit,
    get_coordinator,
)
from app.connectors.core.sync.sync_runner import run_sync_task
from app.connectors.core.sync.task_manager import reindex_task_manager
from app.containers.connector import ConnectorAppContainer
from app.edition_services import (
    get_data_entities_processor_cls,
    sync_executor_enabled,
)
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.messaging.config import Topic
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# Bounded because this runs inline in the sync consumer loop: a longer wait
# stalls every other connector's events behind one delete.
_REMOTE_STOP_WAIT_SEC = float(os.getenv("CONNECTOR_SYNC_DELETE_STOP_WAIT_SEC", "15"))


def _message_timestamp_ms(payload: dict[str, Any]) -> int | None:
    """When the producer stamped this event, if it said.

    Used to tell a stop aimed at *this* request from a stale one left over by a
    previous run: the stop key outlives the sync that prompted it.
    """
    raw = payload.get("createdAtTimestamp")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None



def _running_here(connector_id: str) -> bool:
    """Is a sync for this connector running in *this* process?

    Deliberately the local question, and deliberately synchronous: both callers
    are protecting an in-process object (a connector's HTTP sessions) from being
    closed underneath a running sync, and one of them is a plain `def`. The
    question that reaches other processes costs a round trip and would answer
    something they are not asking.
    """
    coordinator = get_coordinator()
    return coordinator is not None and coordinator.is_running_here(connector_id)


def connector_cache_max() -> int:
    """How many initialised connectors one process may keep. 0 disables the bound."""
    try:
        return int(os.getenv("CONNECTOR_CACHE_MAX", "50"))
    except ValueError:
        return 50


#: Eviction closes the connector's sessions on the loop; hold a reference so the
#: task is not garbage collected mid-flight.
_evict_tasks: set = set()


class EventService:
    """Event service for handling connector-specific events"""

    def __init__(
        self,
        logger: logging.Logger,
        app_container: ConnectorAppContainer,
        graph_provider: IGraphDBProvider,
    ) -> None:
        self.logger = logger
        self.graph_provider = graph_provider
        self.app_container = app_container

    async def _update_app_status(
        self,
        connector_id: str,
        *,
        status: str | None = None,
        is_locked: bool | None = None,
    ) -> None:
        """Update app document status and/or isLocked for a connector.

        Pass status (an AppStatus value string) and/or is_locked (bool).
        Omitted arguments (None) are not written to the DB.
        Always sets updatedAtTimestamp.
        """
        payload: dict[str, Any] = {
            "id": connector_id,
            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
        }
        if status is not None:
            payload["status"] = status
        if is_locked is not None:
            payload["isLocked"] = is_locked
        await self.graph_provider.batch_upsert_nodes(
            [payload], CollectionNames.APPS.value
        )

    def _get_connector(self, connector_id: str) -> BaseConnector | None:
        """
        Get connector instance from app_container.
        """
        connector_key = f"{connector_id}_connector"

        if hasattr(self.app_container, connector_key):
            return getattr(self.app_container, connector_key)()
        elif hasattr(self.app_container, 'connectors_map'):
            cache = self.app_container.connectors_map
            connector = cache.get(connector_id)
            if connector is not None and isinstance(cache, OrderedDict):
                cache.move_to_end(connector_id)  # keep the bound LRU, not FIFO
            return connector

        return None

    async def _store_connector(self, connector_id: str, connector: BaseConnector) -> None:
        """Store a connector instance, releasing the one it replaces and keeping
        the cache bounded.

        Two leaks meet here. A superseded instance still owns an open HTTP
        connection pool, so dropping the reference without closing it leaks that
        pool for the life of the process. And left unbounded the cache itself
        grows one initialised connector — client sessions, credentials, config —
        for every connector the process has ever synced, since only an explicit
        config change or delete ever pops one. At 120 connectors a worker
        reached 3.2 GB RSS and the cgroup OOM-killed three of four workers
        mid-run, taking throughput from 55 rec/s to 9.
        """
        previous = self._get_connector(connector_id)
        connector_key = f"{connector_id}_connector"
        if hasattr(self.app_container, connector_key):
            getattr(self.app_container, connector_key).override(providers.Object(connector))
        else:
            cache = getattr(self.app_container, "connectors_map", None)
            if not isinstance(cache, OrderedDict):
                cache = OrderedDict(cache or {})
                self.app_container.connectors_map = cache

            cache.pop(connector_id, None)
            cache[connector_id] = connector
            self._evict_stale_connectors(cache)

        if previous is None or previous is connector:
            return

        if _running_here(connector_id):
            # cleanup() nulls the connector's client and data source, so closing one
            # mid-sync kills that sync. Leaking the pool is the lesser evil, and is
            # what this did before it started cleaning up at all.
            self.logger.warning(
                f"Replaced the live {connector_id} connector instance while its sync is "
                "running; leaving the previous instance open so the sync can finish"
            )
            return

        self.logger.warning(f"Replaced the live {connector_id} connector instance; cleaning up the previous one")
        try:
            await previous.cleanup()
        except Exception as e:
            self.logger.warning(f"Failed to clean up the replaced {connector_id} connector instance: {e}")

    def _evict_stale_connectors(self, cache: OrderedDict) -> None:
        """Drop least-recently-used connectors that are not mid-sync."""
        limit = connector_cache_max()
        if limit <= 0 or len(cache) <= limit:
            return

        for cached_id in list(cache.keys()):
            if len(cache) <= limit:
                break
            # Evicting a connector that a sync is using would pull the client
            # out from under it, so a busy one keeps its place in the cache.
            if _running_here(cached_id):
                continue
            self._release_connector(cached_id, cache.pop(cached_id))

    def _release_connector(self, connector_id: str, connector: BaseConnector) -> None:
        """Close an evicted connector rather than leaking its sessions instead."""
        cleanup = getattr(connector, "cleanup", None)
        if cleanup is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop: nothing safe to do, and the process is going away

        async def _close() -> None:
            try:
                await cleanup()
            except Exception as e:  # eviction must never fail a sync
                self.logger.warning(
                    "Cleanup failed for evicted connector %s: %s", connector_id, e
                )

        task = loop.create_task(_close(), name=f"evict_connector_{connector_id}")
        _evict_tasks.add(task)
        task.add_done_callback(_evict_tasks.discard)
        self.logger.info(
            "Evicted cached connector %s (cache limit %d)",
            connector_id, connector_cache_max(),
        )

    def _resolve_org_id(self) -> str | None:
        """Optional org id from request/event context"""
        return None

    def _build_data_store(self, org_id: str | None = None) -> GraphDataStore:
        """Build a graph data store"""
        return GraphDataStore(self.logger, self.graph_provider)

    async def _ensure_connector(self, connector_name: str, connector_id: str) -> BaseConnector | None:
        """
        Get connector from memory, or auto-initialize it if missing.
        Handles the case where the init event was missed or the service restarted.
        Checks that the connector is active in the database before initializing.
        """
        # Cacheable only where the cache can be invalidated. This process pops
        # the entry when credentials or filters change (see router); a process
        # without that hook would keep running with the credentials captured at
        # init() long after they changed, and against a connector the database
        # may already have disabled.
        cacheable = not sync_executor_enabled()

        connector = self._get_connector(connector_id) if cacheable else None
        if connector:
            return connector

        async with connector_init_lock(connector_id):
            # Re-check under the lock: every concurrent caller missed the check
            # above, and each would otherwise build a duplicate instance with its
            # own HTTP client and its own rate limiter.
            connector = self._get_connector(connector_id) if cacheable else None
            if connector:
                return connector

            self.logger.warning(
                f"{connector_name} connector {connector_id} not in memory — attempting auto-initialization"
            )
            return await self._auto_initialize_connector(connector_name, connector_id)

    async def _auto_initialize_connector(
        self, connector_name: str, connector_id: str
    ) -> BaseConnector | None:
        """Build and store a connector. Caller must hold ``connector_init_lock``."""
        cacheable = not sync_executor_enabled()
        try:
            connector_doc = await self.graph_provider.get_document(
                document_key=connector_id,
                collection=CollectionNames.APPS.value,
            )
            if not connector_doc:
                self.logger.error(
                    f"Connector {connector_id} not found in database — skipping initialization"
                )
                return None
            if not connector_doc.get("isActive", False):
                self.logger.warning(
                    f"Connector {connector_id} is not active in database — skipping initialization"
                )
                return None
            config_service = self.app_container.config_service()

            # Extract scope, createdBy and org from connector document
            scope = connector_doc.get("scope", "personal")
            created_by = connector_doc.get("createdBy", "")
            last_synced_by = connector_doc.get("lastSyncedBy", "") or None
            org_id = connector_doc.get("orgId") or self._resolve_org_id()
            data_store_provider = self._build_data_store(org_id)

            connector = await ConnectorFactory.initialize_connector(
                name=connector_name,
                logger=self.logger,
                data_store_provider=data_store_provider,
                config_service=config_service,
                connector_id=connector_id,
                scope=scope,
                created_by=created_by,
                org_id=org_id,
                data_entities_processor_cls=get_data_entities_processor_cls(),
                notification_service=self.app_container.connector_notification_service(),
                connector_instance_name=connector_doc.get("name"),
                last_synced_by=last_synced_by,
            )

            if not connector:
                self.logger.error(
                    f"Auto-initialization failed for {connector_name} connector {connector_id}"
                )
                return None

            if cacheable:
                await self._store_connector(connector_id, connector)
            self.logger.info(
                f"Auto-initialized {connector_name} connector {connector_id} successfully"
            )
            return connector
        except Exception as e:
            self.logger.error(
                f"Auto-initialization error for {connector_name} connector {connector_id}: {e}",
                exc_info=True,
            )
            return None

    async def process_event(self, event_type: str, payload: dict[str, Any]) -> bool:
        """Handle connector-specific events - implementing abstract method"""
        try:
            if "." in event_type:
                parts = event_type.split(".")
                connector_name = parts[0].replace(" ", "").lower()
                action = parts[1].lower()
            else:
                self.logger.error(f"Invalid event type format (missing connector prefix): {event_type}")
                return False

            self.logger.info(f"Handling {connector_name} connector event: {action}")

            if action == "init":
                return await self._handle_init(connector_name, payload)
            elif action == "start":
                return await self._handle_start_sync(connector_name, payload)
            elif action == "resync":
                return await self._handle_start_sync(connector_name, payload)
            elif action == "reindex":
                return await self._handle_reindex(connector_name, payload)
            elif action == "delete":
                return await self._handle_delete(connector_name, payload)
            else:
                self.logger.error(f"Unknown {connector_name.capitalize()} connector event type: {action}")
                return False

        except Exception as e:
            self.logger.error(f"Error handling connector event {event_type}: {e}", exc_info=True)
            return False

    async def _handle_init(self, connector_name: str, payload: dict[str, Any]) -> bool:
        """Initializes the event service connector and its dependencies."""
        connector_id = payload.get("connectorId")
        if not connector_id:
            self.logger.error(
                f"'connectorId' is required in the payload for '{connector_name}.init' event."
            )
            return False

        # Shares the lock with the lazy-init paths so an init event and a
        # concurrent stream request cannot each build their own instance.
        async with connector_init_lock(connector_id):
            return await self._build_init_connector(connector_name, payload)

    async def _build_init_connector(self, connector_name: str, payload: dict[str, Any]) -> bool:
        """Build and store the connector. Caller must hold ``connector_init_lock``."""
        try:
            org_id = payload.get("orgId")
            connector_id = payload.get("connectorId")
            if not org_id:
                self.logger.error(f"'orgId' is required in the payload for '{connector_name}.init' event.")
                return False

            self.logger.info(f"Initializing {connector_name} init sync service for org_id: {org_id} and connector_id: {connector_id}")
            config_service = self.app_container.config_service()
            # Built through the edition seam: the store may be org-scoped.
            data_store_provider = self._build_data_store(org_id)


            # Fetch scope and createdBy from database App node
            connector_doc = await self.graph_provider.get_document(
                document_key=connector_id,
                collection=CollectionNames.APPS.value,
            )
            if not connector_doc:
                self.logger.error(f"Connector {connector_id} not found in database")
                return False
            scope = connector_doc.get("scope", "personal")
            created_by = connector_doc.get("createdBy", "")
            last_synced_by = connector_doc.get("lastSyncedBy", "") or None
            connector_instance_name = connector_doc.get("name")
            
            # Use generic connector factory
            connector = await ConnectorFactory.create_connector(
                name=connector_name,
                logger=self.logger,
                data_store_provider=data_store_provider,
                config_service=config_service,
                connector_id=connector_id,
                scope=scope,
                created_by=created_by,
                org_id=org_id,
                data_entities_processor_cls=get_data_entities_processor_cls(),
                notification_service=self.app_container.connector_notification_service(),
                connector_instance_name=connector_instance_name,
                last_synced_by=last_synced_by,
            )

            if not connector:
                self.logger.error(f"❌ Failed to create {connector_name} connector")
                return False

            is_initialized = await connector.init()

            if not is_initialized:
                self.logger.error(f"❌ Failed to initialize {connector_name} connector (init returned False). Not storing in container.")
                return False

            self.logger.info(f"✅ Successfully initialized {connector_name} connector")

            await self._store_connector(connector_id, connector)
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize event service connector {connector_name} for org_id %s: %s", org_id, e, exc_info=True)
            return False

    async def _handle_start_sync(self, connector_name: str, payload: dict[str, Any]) -> bool:
        """Queue immediate start of the sync service"""
        org_id = payload.get("orgId")
        connector_id = payload.get("connectorId")
        full_sync = payload.get("fullSync", False)

        if not org_id:
            self.logger.error("orgId is required in start sync payload")
            return False

        coordinator = get_coordinator()
        if coordinator is None:
            self.logger.error(
                "No sync lease manager configured — refusing to start %s sync for "
                "%s rather than risk two workers syncing it at once",
                connector_name, connector_id,
            )
            return False

        # R6: keep one org's syncs together where a build can. Checked before
        # the connector claim so a handed-back event costs nothing but a
        # republish.
        if await self._org_affinity_bounced(connector_name, payload, org_id):
            return True

        # Admission is the first side effect. Everything below it is either slow
        # (_ensure_connector does OAuth and HTTP) or destructive (the full-sync
        # prep deletes sync points), and both were previously unguarded — two
        # workers could wipe the same connector's sync points.
        #
        # Capacity is decided inside begin(), not after it, so there is no window
        # in which we hold the lease purely to find out we cannot use it. That
        # window is what forced a release-then-write and let another writer's
        # SYNCING be overwritten by our late QUEUED.
        try:
            admission, lease = await coordinator.begin(
                connector_id,
                org_id=org_id,
                message_ts_ms=_message_timestamp_ms(payload),
            )
        except Exception as e:
            # Fail closed. Returning False redelivers the event, which is the
            # right outcome: better a retried sync than two concurrent ones.
            self.logger.error(
                f"Could not admit sync for {connector_id}: {e}", exc_info=True
            )
            return False

        # Every branch below acks. A False result makes the consumer treat the
        # event as a transient failure and redeliver it, stalling the partition
        # behind a connector that is working fine.
        if admission is Admission.AT_CAPACITY:
            await self._mark_queued(connector_id, full_sync=bool(full_sync))
            return True

        if admission is Admission.HELD_ELSEWHERE:
            # The request is carried only by this event, so persist the intent
            # rather than dropping it. Without this a resync asked for while one
            # is running is acked and forgotten, with nothing to tell the caller
            # — and if the running sync started before the connector was fully
            # configured, the connector never syncs at all. The owning sync
            # re-triggers this on completion.
            await self._persist_pending_resync(connector_id, full_sync=bool(full_sync))
            return True

        if admission is not Admission.GRANTED or lease is None:
            # REFUSED_BY_STOP: the user asked to stop this very request while it
            # was in flight. Recording intent here would undo their stop.
            return True

        try:
            result, _ = await self._start_sync_with_lease(
                connector_name, payload, lease, coordinator
            )
            return result
        finally:
            # Only the spawned task releases the lease, via run_sync_task's
            # shielded finalizer. Every path that does *not* spawn one has to
            # release here, or the connector stays un-startable for a full TTL.
            #
            # The lease itself is the record of whether one was spawned:
            # `spawn()` assigns lease.task before anything downstream can raise.
            # A bool returned through the call could not survive the full-sync
            # handler (which returned a hardcoded False) or a CancelledError
            # (which never reaches `except Exception`), and releasing a lease a
            # live task owns lets another process acquire it and run a second sync.
            if lease.task is None:
                await coordinator.end(lease)

    async def _start_sync_with_lease(
        self,
        connector_name: str,
        payload: dict[str, Any],
        lease: SyncLease,
        coordinator: SyncCoordinator,
    ) -> tuple[bool, bool]:
        """Returns (ack the event?, was the lease handed to a sync task?)."""
        org_id = payload.get("orgId")
        connector_id = payload.get("connectorId")
        full_sync = payload.get("fullSync", False)
        handed_off = False
        cacheable = not sync_executor_enabled()

        connector_doc = await self.graph_provider.get_document(
            document_key=connector_id,
            collection=CollectionNames.APPS.value,
        )

        # Ahead of _ensure_connector, which does OAuth and HTTP: no point paying
        # for an init on a connector the database has already disabled.
        if connector_doc and (
            connector_doc.get(ConnectorStateKeys.IS_ACTIVE) is False
            or connector_doc.get(ConnectorStateKeys.IS_AUTHENTICATED) is False
        ):
            self.logger.warning(
                f"Skipping {connector_name} sync for {connector_id}: connector is "
                "disabled or requires re-authentication"
            )
            # Acked, not retried: this is a deliberate state, so redelivering
            # would stall the partition behind a connector that will never run.
            # Re-enabling publishes a fresh event.
            return True, False

        connector = await self._ensure_connector(connector_name, connector_id)
        if not connector:
            self.logger.error(f"{connector_name.capitalize()} {connector_id} connector could not be initialized")
            return False, False

        synced_by = payload.get("syncedBy", "")
        if synced_by:
            await self.graph_provider.update_node(
                connector_id, CollectionNames.APPS.value,
                {"lastSyncedBy": synced_by},
            )
            connector.last_synced_by = synced_by

        pending_full_sync = False
        if connector_doc:
            pending_full_sync = bool(connector_doc.get(ConnectorStateKeys.PENDING_FULL_SYNC, False))

        # Merge payload fullSync with pending flag - if either is true, do full sync
        effective_full_sync = bool(full_sync) or pending_full_sync

        if pending_full_sync:
            self.logger.info(f"Connector {connector_id} has pendingFullSync flag set, will perform full sync")

        self.logger.info(f"Starting {connector_name} sync service for org_id: {org_id}, full_sync: {effective_full_sync} (payload: {full_sync}, pending: {pending_full_sync})")

        if effective_full_sync:
            # No "is one already running" check here: begin() above already
            # settled admission, and it is this call that holds the claim. Asking
            # again declined every full sync against its own lease -- a newly
            # created connector never synced at all.
            # --- Full sync: acquire lock for the prep phase ---
            try:
                await self._update_app_status(
                    connector_id,
                    status=AppStatus.FULL_SYNCING.value,
                    is_locked=True,
                )
                self.logger.info(f"🔒 Set status=FULL_SYNCING, isLocked=True for connector {connector_id}")
            except Exception as lock_err:
                self.logger.error(f"❌ Failed to set lock for connector {connector_id}: {lock_err}")
                return False, False

            try:
                # Delete sync points
                self.logger.info(f"Full sync requested - deleting sync points for connector {connector_id}")
                try:
                    deleted_count, success = await self.graph_provider.delete_sync_points_by_connector_id(
                        connector_id=connector_id
                    )
                    if success:
                        self.logger.info(f"✅ Successfully deleted {deleted_count} sync points for connector {connector_id}")
                    else:
                        self.logger.warning(f"⚠️ Failed to delete sync points for connector {connector_id}, continuing with sync")
                except Exception as sync_point_error:
                    self.logger.error(f"❌ Error deleting sync points for connector {connector_id}: {sync_point_error}")
                    self.logger.warning("Continuing with sync despite sync point deletion failure")

                # Delete sync edges
                try:
                    deleted_edges, success = await self.graph_provider.delete_connector_sync_edges(
                        connector_id=connector_id
                    )
                    if success:
                        self.logger.info(f"Successfully deleted {deleted_edges} sync edges for connector {connector_id}")
                    else:
                        self.logger.warning(f"Failed to delete some sync edges for connector {connector_id}, continuing with sync")
                except Exception as edge_error:
                    self.logger.error(f"Error deleting connector sync edges for {connector_id}: {edge_error}")

                # Schedule the background sync task. Holding the lease means
                # no other task in this process can be running this connector,
                # so start_if_idle declining here is a bug state, not a race —
                # it is logged loudly rather than treated as normal.
                task = await coordinator.spawn(
                    lease,
                    run_sync_task(
                        connector,
                        connector_id,
                        self.graph_provider,
                        self.logger,
                        start_status=AppStatus.FULL_SYNCING.value,
                        lease=lease,
                        coordinator=coordinator,
                        close_connector=not cacheable,
                        resync_spec=SyncSpec(
                            connector_id=connector_id,
                            connector_name=connector_name,
                            org_id=org_id,
                        ),
                    ),
                )

                if task is None:
                    # A sync is already running for this connector in this
                    # process. Declining beats cancelling and restarting it —
                    # that discards work already done, and a sync slower than its
                    # own trigger interval would restart for ever and never
                    # finish. Record the intent so it is re-issued rather than
                    # silently dropped; the running sync hands it back from its
                    # finalizer.
                    self.logger.info(
                        f"Full sync for {connector_id} declined: one is already running. "
                        f"Recorded pendingResync for re-issue when it finishes."
                    )
                    await self._persist_pending_resync(connector_id, full_sync=True)
                else:
                    handed_off = True
                    self.logger.info(f"Started full sync task for {connector_name} {connector_id}")

                    # Clear only when we consumed a persisted pending flag (avoids redundant writes on manual full sync).
                    if pending_full_sync:
                        try:
                            await self.graph_provider.update_node(
                                connector_id,
                                CollectionNames.APPS.value,
                                {ConnectorStateKeys.PENDING_FULL_SYNC: False},
                            )
                            self.logger.info(f"Cleared pendingFullSync flag for connector {connector_id}")
                        except Exception as clear_err:
                            self.logger.error(f"Failed to clear pendingFullSync flag for connector {connector_id}: {clear_err}")

            except Exception as e:
                self.logger.error(f"❌ Failed during full sync prep for {connector_id}: {e}")
                # Release lock immediately so the connector is not stuck
                try:
                    await self._update_app_status(connector_id, status=AppStatus.IDLE.value, is_locked=False)
                except Exception as revert_err:
                    self.logger.error(f"❌ Failed to revert lock for connector {connector_id}: {revert_err}")
                return False, False

            # Prep done and task scheduled — release the lock now.
            # Status stays FULL_SYNCING until run_sync() finishes.
            try:
                await self._update_app_status(connector_id, is_locked=False)
                self.logger.info(f"🔓 Released lock for connector {connector_id} (status remains FULL_SYNCING)")
            except Exception as unlock_err:
                self.logger.error(f"❌ Failed to release lock for connector {connector_id}: {unlock_err}")
                # Non-fatal: sync task is already running; log and continue

        else:
            # --- Normal sync: run_sync_task writes SYNCING as its first act ---
            task = await coordinator.spawn(
                lease,
                run_sync_task(
                    connector,
                    connector_id,
                    self.graph_provider,
                    self.logger,
                    lease=lease,
                    coordinator=coordinator,
                    close_connector=not cacheable,
                    resync_spec=SyncSpec(
                        connector_id=connector_id,
                        connector_name=connector_name,
                        org_id=org_id,
                    ),
                ),
            )
            if task is None:
                # Same reasoning as the full-sync path above: decline, record,
                # re-issue on completion.
                self.logger.info(
                    f"Sync for {connector_id} declined: one is already running. "
                    f"Recorded pendingResync for re-issue when it finishes."
                )
                await self._persist_pending_resync(connector_id)
            else:
                handed_off = True
                self.logger.info(f"Started sync task for {connector_name} {connector_id}")

        return True, handed_off

    async def _org_affinity_bounced(
        self, connector_name: str, payload: dict[str, Any], org_id: str | None
    ) -> bool:
        """Whether this event was handed back for another process to run.

        One process here, so there is nowhere to hand it to.
        """
        return False

    async def _await_remote_sync_stop(self, connector_id: str) -> bool:
        """Ask a sync on another process to stop, and wait briefly for it.

        The owner notices within one heartbeat interval, so the wait only needs
        to cover that plus a little slack. Returns whether the connector was
        actually free by the end.
        """
        coordinator = get_coordinator()
        if coordinator is None:
            return True

        try:
            if not await coordinator.request_stop(connector_id):
                return True
        except Exception as e:
            self.logger.error(f"Could not request stop for {connector_id}: {e}")
            return True

        deadline = time.monotonic() + _REMOTE_STOP_WAIT_SEC
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            try:
                if not await coordinator.is_running(connector_id):
                    return True
            except Exception:
                return True

        self.logger.warning(
            "Connector %s was still syncing on another process after %ss; "
            "continuing anyway",
            connector_id, _REMOTE_STOP_WAIT_SEC,
        )
        return False

    async def _mark_queued(
        self, connector_id: str, *, full_sync: bool = False
    ) -> None:
        """Accepted but not started: at the concurrency limit.

        Status is what the UI reads, and pendingResync is what actually gets the
        sync run later — the drain in the finalizer re-issues it when a slot
        frees. Without the status the connector would sit showing IDLE with a
        sync pending, which reads as the request having been dropped.
        """
        updates: dict[str, Any] = {
            ConnectorStateKeys.PENDING_RESYNC: True,
            "status": AppStatus.QUEUED.value,
            # Stamped so the drain can tell a fresh queue entry from one whose
            # re-issued event never arrived.
            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
        }
        if full_sync:
            updates[ConnectorStateKeys.PENDING_FULL_SYNC] = True
        try:
            await self.graph_provider.update_node(
                connector_id, CollectionNames.APPS.value, updates
            )
            self.logger.info(
                "Queued %s (fullSync=%s): at the sync concurrency limit of %d",
                connector_id, full_sync, _safe_limit(self.logger),
            )
        except Exception as e:
            self.logger.error(f"Failed to mark {connector_id} queued: {e}")

    async def _persist_pending_resync(
        self, connector_id: str, *, full_sync: bool = False
    ) -> None:
        """Remember a resync we had to decline, so it is re-issued later."""
        updates: dict[str, Any] = {ConnectorStateKeys.PENDING_RESYNC: True}
        if full_sync:
            updates[ConnectorStateKeys.PENDING_FULL_SYNC] = True
        try:
            await self.graph_provider.update_node(
                connector_id, CollectionNames.APPS.value, updates
            )
            self.logger.info(
                "Recorded pending resync for %s (fullSync=%s) — it was declined "
                "because a sync is already running", connector_id, full_sync,
            )
        except Exception as e:
            self.logger.error(
                f"Failed to persist pending resync for {connector_id}: {e}"
            )

    @staticmethod
    def _reindex_task_key(
        connector_id: str,
        record_id: str | None,
        record_group_id: str | None,
        depth: int,
        user_key: str | None,
        status_filters: list[str] | None,
    ) -> str:
        """Identify a reindex request by everything that changes its result set.

        A redelivered event must collapse onto the running task, but two genuinely
        different requests (reindex-FAILED then reindex-AUTO_INDEX_OFF, or the same
        folder at a different depth) must be allowed to run side by side.
        """
        target = record_id or record_group_id or "*"
        filters = ",".join(sorted(status_filters or []))
        return f"reindex:{connector_id}:{target}:{depth}:{user_key or '*'}:{filters}"

    async def _handle_reindex(self, connector_name: str, payload: dict[str, Any]) -> bool:
        """Validate a reindex event and hand the work to a background task.

        Returns as soon as the task is scheduled. The reindex itself can run for
        hours, and this handler is awaited inline by the Kafka poll loop — doing the
        work here would stall polling past max.poll.interval.ms, get the consumer
        evicted, and have the event redelivered forever.

        Supports three modes:
        1. Record with depth: recordId + depth - reindex a folder and its children
        2. Record group with depth: recordGroupId + depth - reindex all records in a record group
        3. Status-based: statusFilters - reindex records by indexing status (e.g., FAILED)
        """
        connector_id = payload.get("connectorId")
        try:

            org_id = payload.get("orgId")
            record_id = payload.get("recordId")
            record_group_id = payload.get("recordGroupId")
            depth = payload.get("depth", 0)
            raw_status_filters = payload.get("statusFilters")
            user_key = payload.get("userKey")
            # Parent-scoped modes: optional filter; connector-wide mode: default FAILED,
            # except for KB connectors, where a KB-wide reindex means "reindex everything".
            status_filters: list[str] | None = None
            if record_id is not None or record_group_id is not None:
                status_filters = raw_status_filters if raw_status_filters else None
            elif connector_name == Connectors.KNOWLEDGE_BASE.value.lower():
                status_filters = raw_status_filters if raw_status_filters else None
            else:
                status_filters = raw_status_filters if raw_status_filters else ["FAILED"]

            if not org_id:
                raise ValueError("orgId is required")

            if not connector_id:
                self.logger.error("connectorId is required in payload for reindex event")
                return False

            connector = await self._ensure_connector(connector_name, connector_id)
            if not connector:
                self.logger.error(f"{connector_name.capitalize()} {connector_id} connector could not be initialized")
                return False

            # Reject a connector whose app name maps to no known Connectors member,
            # while we can still report it synchronously.
            enum_key = connector.app.get_app_name().name.upper().replace(" ", "_")
            if not getattr(Connectors, enum_key, None):
                self.logger.error(f"Unknown connector name: {connector_name}")
                return False

            task_key = self._reindex_task_key(
                connector_id, record_id, record_group_id, depth, user_key, status_filters
            )
            task = await reindex_task_manager.start_if_idle(
                task_key,
                self._run_reindex(
                    connector=connector,
                    connector_name=connector_name,
                    connector_id=connector_id,
                    org_id=org_id,
                    record_id=record_id,
                    record_group_id=record_group_id,
                    depth=depth,
                    user_key=user_key,
                    status_filters=status_filters,
                ),
            )
            if task is None:
                self.logger.info(
                    f"Reindex already running for {connector_name} {connector_id} ({task_key}) - ignoring duplicate event"
                )
            return True

        except Exception as e:
            self.logger.error(f"Failed to handle reindex for {connector_name.capitalize()} {connector_id}: {str(e)}", exc_info=True)
            return False

    async def _run_reindex(
        self,
        connector: BaseConnector,
        connector_name: str,
        connector_id: str,
        org_id: str,
        record_id: str | None,
        record_group_id: str | None,
        depth: int,
        user_key: str | None,
        status_filters: list[str] | None,
    ) -> None:
        """Walk every matching record once and hand each batch to the connector.

        Pages with a keyset cursor rather than an offset. The result set mutates
        while we iterate — records leave it as we set them NOT_STARTED, and the
        indexing service can push them back into it by marking them FAILED — so a
        positional offset both skips records and never terminates. A cursor only
        moves forward, so every record is visited at most once and the walk is a
        single pass over the key range regardless of what changes underneath it.
        """
        if record_id is not None:
            self.logger.info(f"Starting reindex for {connector_name}, {connector_id} connector record {record_id} with depth {depth}")
        elif record_group_id is not None:
            self.logger.info(f"Starting reindex for {connector_name}, {connector_id} connector record group {record_group_id} with depth {depth}")
        else:
            self.logger.info(f"Starting reindex for {connector_name}, {connector_id} connector with status filters: {status_filters}")

        batch_size = 100
        after_key: str | None = None
        total_processed = 0

        # A record the indexing service is actively working on must not be
        # republished: that would run two pipelines against the same
        # virtualRecordId. Everything else, including AUTO_INDEX_OFF, is fair game.
        exclude_statuses = [ProgressStatus.IN_PROGRESS.value]

        while True:
            if record_id is not None:
                # Mode 1: Reindex a folder and its children
                records = await self.graph_provider.get_records_by_parent_record(
                    parent_record_id=record_id,
                    connector_id=connector_id,
                    org_id=org_id,
                    depth=depth,
                    user_key=user_key,
                    limit=batch_size,
                    status_filters=status_filters,
                    after_key=after_key,
                    exclude_statuses=exclude_statuses,
                )
            elif record_group_id is not None:
                # Mode 2: Reindex records in a record group
                records = await self.graph_provider.get_records_by_record_group(
                    record_group_id=record_group_id,
                    connector_id=connector_id,
                    org_id=org_id,
                    depth=depth,
                    user_key=user_key,
                    limit=batch_size,
                    status_filters=status_filters,
                    after_key=after_key,
                    exclude_statuses=exclude_statuses,
                )
            else:
                # Mode 3: Reindex by status
                records = await self.graph_provider.get_records_by_status(
                    org_id=org_id,
                    connector_id=connector_id,
                    status_filters=status_filters,
                    limit=batch_size,
                    after_key=after_key,
                    exclude_statuses=exclude_statuses,
                    is_placeholder=False,
                )

            fetched_count = len(records)
            last_id = records[-1].id if records else None
            records = [r for r in records if not r.is_placeholder]

            if not records:
                if not last_id or fetched_count < batch_size:
                    break
                after_key = last_id
                continue

            self.logger.info(f"Processing batch of {len(records)} records (after_key: {after_key})")

            # Advance the cursor from the last row of the batch, before doing any
            # work: a record that fails and flips back into the filter must not be
            # picked up again by this run. Taking it from anything other than the
            # final row would rewind the cursor and re-fetch the batch forever.
            if not last_id:
                self.logger.error(
                    f"Last record of batch has no usable id - stopping reindex for "
                    f"{connector_id}; cannot advance the cursor safely"
                )
                break
            after_key = last_id

            record_ids_to_update = [r.id for r in records if r.id]
            if record_ids_to_update:
                await self.graph_provider.update_indexing_status_for_record_ids(
                    record_ids_to_update, ProgressStatus.NOT_STARTED.value
                )

            # Connectors that publish via on_new_records drop AUTO_INDEX_OFF records;
            # reindex is an explicit user action, so clear that in memory too.
            for record in records:
                record.indexing_status = ProgressStatus.NOT_STARTED.value

            await connector.reindex_records(records)

            total_processed += len(records)

            if fetched_count < batch_size:
                break

        self.logger.info(f"✅ Completed reindex for {connector_name} {connector_id} connector. Total records processed: {total_processed}")

    async def _handle_delete(self, connector_name: str, payload: dict[str, Any]) -> bool:
        """
        Handle the async connector deletion event.

        Flow:
        1. Call delete_connector_instance on the graph DB
        2. On success: publish bulkDeleteRecords for Qdrant cleanup, delete etcd config
        3. On failure: revert status to null so the connector is not stuck
        """
        org_id = payload.get("orgId")
        connector_id = payload.get("connectorId")
        previous_is_active = payload.get("previousIsActive", False)

        if not org_id or not connector_id:
            self.logger.error("'orgId' and 'connectorId' are required in the delete payload")
            return False

        self.logger.info(f"🗑️ Processing async deletion for {connector_name} connector {connector_id}")

        try:
            # Stop any sync before deleting the data underneath it.
            #
            # Local tasks are cancelled and awaited as before. A sync on another
            # process gets a stop flag and a bounded wait — bounded because this
            # runs inline in the sync consumer's message loop, so waiting a full
            # lease TTL here would stall every other connector's events behind
            # one delete. If it does not stop in time we proceed anyway and say
            # so: the alternative is refusing the user's delete outright, and a
            # wedged owner would never release before the TTL regardless.
            # Clear the owed resync before stopping anything. The finalizer of
            # the sync we are about to cancel reads this flag and hands the
            # request back; that event would then arrive for a connector whose
            # rows are gone, fail to build, and be redelivered forever. The stop
            # endpoint clears it first for exactly this reason.
            try:
                await self.graph_provider.update_node(
                    connector_id,
                    CollectionNames.APPS.value,
                    {ConnectorStateKeys.PENDING_RESYNC: False},
                )
            except Exception as clear_err:
                self.logger.warning(
                    f"Could not clear pendingResync for {connector_id} "
                    f"before delete: {clear_err}"
                )

            coordinator = get_coordinator()
            if coordinator is not None:
                await coordinator.cancel_and_wait(connector_id)
            await reindex_task_manager.cancel_by_prefix(f"reindex:{connector_id}:")
            await self._await_remote_sync_stop(connector_id)

            # Delete from graph DB
            result = await self.graph_provider.delete_connector_instance(
                connector_id=connector_id,
                org_id=org_id
            )

            if not result.get("success"):
                raise Exception(result.get("error", "Unknown deletion failure from graph DB"))

            self.logger.info(
                f"✅ Graph DB deletion complete for connector {connector_id}. "
                f"Records: {result.get('deleted_records_count', 0)}"
            )

            # Publish bulkDeleteRecords so the indexing service cleans up Qdrant embeddings.
            # connectorName lets the consumer resolve which collection(s) this
            # connector's data lives in under a per-connector-type strategy; it is
            # read with .get() there so a redelivered message from an older
            # producer still degrades to today's single-collection behaviour.
            virtual_record_ids = result.get("virtual_record_ids", [])
            if virtual_record_ids:
                try:
                    await self.app_container.messaging_producer.send_message(
                        topic="record-events",
                        message={
                            "eventType": EventTypes.BULK_DELETE_RECORDS.value,
                            "payload": {
                                "orgId": org_id,
                                "connectorId": connector_id,
                                "connectorName": result.get("connector_name"),
                                "virtualRecordIds": virtual_record_ids,
                                "totalRecords": len(virtual_record_ids),
                            },
                            "timestamp": get_epoch_timestamp_in_ms(),
                        },
                    )
                    self.logger.info(f"✅ Published bulkDeleteRecords for {len(virtual_record_ids)} records")
                except Exception as kafka_err:
                    self.logger.error(
                        f"❌ Failed to publish bulkDeleteRecords for connector {connector_id}: {kafka_err}. "
                        f"Embeddings may persist in Qdrant — manual cleanup may be required."
                    )

            # Delete connector credentials from etcd/config store
            try:
                config_service = self.app_container.config_service()
                config_path = f"/services/connectors/{connector_id}/config"
                await config_service.delete_config(config_path)
                self.logger.info(f"✅ Deleted etcd config for connector {connector_id}")
            except Exception as config_err:
                self.logger.error(
                    f"❌ Failed to delete etcd config for connector {connector_id}: {config_err}. "
                    f"Orphaned configuration may remain."
                )

            self.logger.info(f"✅ Async deletion complete for connector {connector_id}")
            return True

        except Exception as e:
            self.logger.error(
                f"❌ Async deletion failed for connector {connector_id}: {e}",
                exc_info=True
            )
            try:
                await self.graph_provider.batch_upsert_nodes(
                    [{
                        "id": connector_id,
                        "status": None,
                        "isActive": previous_is_active,
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                    }],
                    CollectionNames.APPS.value
                )
                self.logger.info(f"↩️ Reverted status for connector {connector_id}")
            except Exception as revert_err:
                self.logger.error(
                    f"❌ Failed to revert status for connector {connector_id}: {revert_err}. "
                    f"Connector may be stuck in DELETING state."
                )
            return False
