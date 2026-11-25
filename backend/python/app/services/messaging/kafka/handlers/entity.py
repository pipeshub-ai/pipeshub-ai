import asyncio
import logging
import time
from typing import TYPE_CHECKING
from urllib.parse import quote
from uuid import UUID, uuid4, uuid5

from app.agents.agent_loop.skills.graph_store import erase_skill_author
from app.api.middlewares.caller_role import fetch_own_identity
from app.config.constants.arangodb import (
    AccountType,
    AppGroups,
    AppStatus,
    CollectionNames,
    Connectors,
    ConnectorScopes,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.base.connector.connector_service import (
    connector_sync_lock,
    note_sync_started,
)
from app.connectors.core.base.connector.instance_lock import connector_init_lock
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    USER_LINK_MARK_TTL_MS,
)
from app.connectors.core.base.data_store.graph_data_store import (
    GraphDataStore,
    ensure_written,
)
from app.connectors.core.base.event_service.event_service import BaseEventService
from app.connectors.core.constants import ConnectorStateKeys
from app.connectors.core.factory.connector_factory import ConnectorFactory
from app.connectors.core.sync.sync_coordinator import get_coordinator, stop_wait_sec
from app.connectors.core.sync.task_manager import reindex_task_manager
from app.connectors.services.event_service import evict_cached_connector
from app.containers.connector import (
    ConnectorAppContainer,
)
from app.control_plane.directory.events import DIRECTORY_EVENT_TYPES
from app.core import org_context
from app.edition_services import get_data_entities_processor_cls
from app.exceptions.non_retryable import NonRetryableError
from app.harness.knowledge.service import KnowledgePublications
from app.modules.indexing.record_diagnostics import WriteKind
from app.modules.indexing.record_diagnostics import reference as run_reference
from app.modules.indexing.record_republish import record_event
from app.schema.node_validator import SchemaValidationError
from app.services.cache.invalidation_hooks import notify_principals_changed
from app.services.graph_db.common.record_visibility import (
    RecordVisibility,
    is_live_record,
)
from app.services.graph_db.entity_index_queries import APP_STATUS_DELETING
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.paid_cache import access as paid_cache_access
from app.services.paid_cache.repurge import schedule_org_repurge
from app.telemetry.modules.schema_metrics import record_schema_rejection
from app.utils import error_class
from app.utils.logging_fields import log_fields
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.utils.user_errors import CONNECTOR_OFF

if TYPE_CHECKING:
    from app.connectors.core.base.data_store.data_store import TransactionStore

_REQUEUE_PAGE_SIZE = 100
# A burst of added users is collected for this long, then linked by one pass per connector.
_LINK_DEBOUNCE_SEC = 10
# A pass waits for a running sync (it may be past its user step) for at most this many debounces.
_LINK_MAX_DEFERRALS = 360
# Parked by a turn-off, as opposed to AUTO_INDEX_OFF from the connector's own
# indexing filters, which carries no such reason.
_PARKED_WHILE_OFF = {
    "indexingStatus": ProgressStatus.AUTO_INDEX_OFF.value,
    "reason": CONNECTOR_OFF,
}


# Events that change who a person is or which apps they reach, and with it what they may read. Each moves the
# viewer-scope version (tests/unit/services/permissions/test_permission_writers_bump.py keeps this list complete).
PRINCIPAL_EVENTS = frozenset({"userAdded", "userUpdated", "userDeleted", "appEnabled", "appDisabled", "orgDeleted"})


# Each one that misses means another toggle committed meanwhile; this many in a row is someone
# toggling non-stop, and the event is failed rather than looped on.
_DISABLE_WRITE_ATTEMPTS = 5


# A new login user's graph node is keyed by (org, userId), not by a random id, so every writer of one user (the
# userAdded event, a redelivery of it, the user's own first requests, any number of workers) upserts the same node:
# the store's unique key on the id makes them converge. (A default knowledge base is not keyed this way: its key
# would come back after a delete, and cleanup queued for the deleted one would reach the new one.)
_LOGIN_USER_NAMESPACE = UUID("6f1c1d2e-5a0b-4f7e-9c3d-2b8e4a6f0c11")


def _login_user_key(org_id: str, user_id: str) -> str:
    return str(uuid5(_LOGIN_USER_NAMESPACE, f"{org_id}:{user_id}"))


# Rounds of reading a user's default and writing one, lost each time to another writer of the same user.
_DEFAULT_KB_CLAIM_ATTEMPTS = 5


class _DefaultKnowledgeBaseNamedMeanwhile(Exception):
    """Another writer named the user's default after it was read: this transaction is rolled back."""


def _superseded_by_a_newer_toggle(payload: dict, app_doc: dict) -> bool:
    """Whether a toggle numbered after this event's turned the connector back on.

    Only a sync toggle numbers its event; any other appDisabled (a refresh token gone
    bad, a delete) carries no number and always disables. A newer toggle that left the
    connector off is not a reason to skip: disabling again stops what the first one
    should have, and writes the same value."""
    sent = payload.get(ConnectorStateKeys.TOGGLE_SEQ)
    current = app_doc.get(ConnectorStateKeys.TOGGLE_SEQ)
    if sent is None or current is None:
        return False
    return int(sent) < int(current) and bool(app_doc.get(ConnectorStateKeys.IS_ACTIVE))


def _enable_superseded_by_a_newer_toggle(payload: dict, app_doc: dict) -> bool:
    """Whether a toggle numbered after this appEnabled has been written since.

    A newer enable sends its own event, which starts the sync; a newer disable left the
    connector off, so this one must not start a sync on it. Either way this event has no
    work left. Two enables that overlap both write and send, and without this check both
    events start a sync."""
    sent = payload.get(ConnectorStateKeys.TOGGLE_SEQ)
    current = app_doc.get(ConnectorStateKeys.TOGGLE_SEQ)
    if sent is None or current is None:
        return False
    return int(sent) < int(current)


class EntityEventService(BaseEventService):
    def __init__(
        self,
        logger: logging.Logger,
        graph_provider: IGraphDBProvider,
        app_container: ConnectorAppContainer,
        *,
        publications: KnowledgePublications | None = None,
    ) -> None:
        self.logger = logger
        self.graph_provider = graph_provider
        self.graph_data_store = GraphDataStore(logger, graph_provider)
        self._link_state: dict[str, dict] = {}
        self.app_container = app_container
        # Erasure never reads the publications flag (C-14 rule), so no configuration is needed here.
        self._publications = publications or KnowledgePublications(graph_provider, None)

    async def process_event(self, event_type: str, payload: dict) -> bool:
        """Handle entity-related events by calling appropriate handlers"""
        try:
            return await self._dispatch(event_type, payload)
        finally:
            if event_type in PRINCIPAL_EVENTS:
                # Success or not: a handler that failed half way may have changed edges. Nothing here widens access.
                await notify_principals_changed(payload.get("orgId") if isinstance(payload, dict) else None)

    async def _dispatch(self, event_type: str, payload: dict) -> bool:
        try:
            if event_type == "orgCreated":
                return await self._handle_org_created(payload)
            elif event_type == "orgUpdated":
                return await self._handle_org_updated(payload)
            elif event_type == "orgDeleted":
                return await self._handle_org_deleted(payload)
            elif event_type == "userAdded":
                return await self._handle_user_added(payload)
            elif event_type == "userUpdated":
                return await self._handle_user_updated(payload)
            elif event_type == "userDeleted":
                return await self._handle_user_deleted(payload)
            elif event_type == "appEnabled":
                return await self._handle_app_enabled(payload)
            elif event_type == "appDisabled":
                return await self._handle_app_disabled(payload)
            elif event_type in DIRECTORY_EVENT_TYPES:
                # Node's directory caches follow these (CPD-9); this service keeps nothing of them.
                return True
            else:
                raise NonRetryableError(f"unknown entity event type {event_type}")
        except SchemaValidationError as e:
            # Permanent: redelivering the event cannot make the payload valid. Handled, not retried.
            # The data processor marks an error it already logged and counted: report it once.
            if not getattr(e, "schema_reported", False):
                # Where and which rule, never the message: jsonschema's text quotes the rejected value.
                rule = e.original_error
                self.logger.error(
                    f"Entity event {event_type} rejected by schema validation (collection={e.collection})",
                    extra={"schema_path": getattr(rule, "json_path", None), "validator": getattr(rule, "validator", None)},
                )
                record_schema_rejection(e.collection)
            return True

    async def _handle_sync_event(self,event_type: str, value: dict) -> bool:
        """Handle sync-related events by sending them to the sync-events topic"""
        try:
            # Prepare the message
            message = {
                'eventType': event_type,
                'payload': value,
                'timestamp': get_epoch_timestamp_in_ms()
            }

            # Keyed by connector like Node's sync events: an unkeyed start could
            # land on another partition than that connector's resyncs and be
            # consumed out of order with them.
            await self.app_container.messaging_producer.send_message(
                topic='sync-events',
                message=message,
                key=(value or {}).get("connectorId") or None,
            )

            self.logger.info(f"Successfully sent sync event: {event_type}")
            return True

        except Exception as e:
            error_class.log_failure(self.logger, "entity_event.step_failed", e, step="send_sync_event")
            return False

    # ORG EVENTS
    async def _handle_org_created(self, payload: dict, *, bootstrap: bool = False) -> bool:
        """Handle organization creation event.

        ``bootstrap``: written for a caller's first request (ensure_user), not by the event. It only creates the org
        where there is none, so an org deleted after Node answered is not brought back.
        """

        accountType = (
            AccountType.ENTERPRISE.value
            if payload["accountType"] in [AccountType.BUSINESS.value, AccountType.ENTERPRISE.value]
            else AccountType.INDIVIDUAL.value
        )
        try:
            org_id = payload["orgId"]
            org_data = {
                "_key": org_id,
                "name": payload.get("registeredName", "Individual Account"),
                "accountType": accountType,
                "isActive": True,
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }

            # Convert _key to id for provider
            org_data["id"] = org_data.pop("_key")

            if bootstrap:
                created = await self.graph_provider.create_node_if_absent(
                    org_id, CollectionNames.ORGS.value, {k: v for k, v in org_data.items() if k != "id"}
                )
                if not created:
                    return True  # it is there: the event, or what made it, did the rest
            else:
                await self.graph_provider.batch_upsert_nodes(
                    [org_data], CollectionNames.ORGS.value
                )

            # raise_on_error: a failed read would otherwise create the org with no
            # department links and acknowledge the event.
            departments = await self.graph_provider.get_nodes_by_filters(
                collection=CollectionNames.DEPARTMENTS.value,
                filters={"orgId": None},
                raise_on_error=True,
            )

            # Create relationships between org and departments
            org_department_relations = []
            for department in departments:
                dept_id = department.get("id") or department.get("_key")
                relation_data = {
                    "from_id": payload["orgId"],
                    "from_collection": CollectionNames.ORGS.value,
                    "to_id": dept_id,
                    "to_collection": CollectionNames.DEPARTMENTS.value,
                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                }
                org_department_relations.append(relation_data)

            if org_department_relations:
                await self.graph_provider.batch_create_edges(
                    org_department_relations,
                    CollectionNames.ORG_DEPARTMENT_RELATION.value,
                )
                self.logger.info(
                    f"Successfully created organization: {payload['orgId']} and relationships with departments"
                )
            else:
                self.logger.info(
                    f"Successfully created organization: {payload['orgId']}"
                )

            # Create "All" team for the org (first user will be added with OWNER in userAdded)
            await self._create_all_team_for_org(payload['orgId'], payload.get('userId'))

            return True

        except SchemaValidationError:
            raise  # permanent: _dispatch logs it once and the event is not redelivered
        except Exception as e:
            error_class.log_failure(self.logger, "entity_event.failed", e)
            raise

    async def _handle_org_updated(self, payload: dict) -> bool:
        """Handle organization update event"""
        try:
            org_data = {
                "_key": payload["orgId"],
                "name": payload["registeredName"],
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }

            # Convert _key to id for provider
            org_data["id"] = org_data.pop("_key")

            # Batch upsert org
            await self.graph_provider.batch_upsert_nodes(
                [org_data], CollectionNames.ORGS.value
            )
            self.logger.info(
                f"Successfully updated organization: {payload['orgId']}"
            )
            return True

        except SchemaValidationError:
            raise  # permanent: _dispatch logs it once and the event is not redelivered
        except Exception as e:
            error_class.log_failure(self.logger, "entity_event.failed", e)
            raise

    async def _handle_org_deleted(self, payload: dict) -> bool:
        """Handle organization deletion event"""
        try:
            org_id = payload["orgId"]
            # Before the org is marked inactive: a failed erasure leaves the org active and reaches the consumer's
            # classifier like any other failure of this event (logged once below, retried only when transient).
            await self._publications.erase_org(org_id)
            org_data = {
                "_key": org_id,
                "isActive": False,
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }

            # Convert _key to id for provider
            org_data["id"] = org_data.pop("_key")

            # Batch upsert org with isActive = False
            await self.graph_provider.batch_upsert_nodes(
                [org_data], CollectionNames.ORGS.value
            )
            await notify_principals_changed(org_id)  # before the cleanup below; see process_event
            self.logger.info(
                f"Successfully soft-deleted organization: {payload['orgId']}"
            )
            await self._remove_mcp_data("org", payload["orgId"])
            await self._remove_personal_toolsets("org", payload["orgId"])
            await self._remove_api_toolset_data(payload["orgId"])
            # Erasure: every stored model output of the org. A storage failure raises, so the event is redelivered.
            await paid_cache_access.paid_cache_for(self.app_container.config_service(), self.logger).purge_org(org_id)
            # Redis can lose the marker while a parse that outlived the org is still running: one durable delayed
            # purge for the org removes whatever such a write left. A publish failure raises (redelivery).
            producer = self.app_container.messaging_producer
            if producer is None:
                # Nothing to publish with: the purge above already ran and the org is soft-deleted, so the event
                # completes. Redelivering cannot create a producer. Never log the payload.
                self.logger.warning("Org deleted, but no messaging producer is configured: the delayed paid-result re-purge was not scheduled")
            else:
                await schedule_org_repurge(producer, org_id)
            return True

        except SchemaValidationError:
            raise  # permanent: _dispatch logs it once and the event is not redelivered
        except Exception as e:
            error_class.log_failure(self.logger, "entity_event.failed", e)
            raise

    # USER EVENTS
    async def ensure_user(self, org_id: str, user_id: str) -> bool:
        """Write a user of ``org_id`` into the graph when the events announcing them have not been consumed yet.

        A brand-new organisation's admin calls before its orgCreated and userAdded events reach this service. Callers
        pass only the request's own verified caller and org (app.utils.user_lookup.get_caller_user). The data is
        Node's live records of exactly that user and org: a deleted or disabled account, or a record of another org,
        writes nothing. The writes are the events' own handlers, so the events, when they arrive, find everything in
        place.
        """
        if not org_id or not user_id:
            return False
        try:
            config_service = self.app_container.config_service()
            user = await fetch_own_identity(
                config_service, org_id, user_id, f"/api/v1/users/internal/{quote(user_id, safe='')}"
            )
            if (
                not user or str(user.get("orgId")) != org_id or user.get("isDeleted") or user.get("isDisabled")
                or not user.get("email")
            ):
                return False
            if not await self.graph_provider.get_document(org_id, CollectionNames.ORGS.value, raise_on_error=True):
                org = await fetch_own_identity(config_service, org_id, user_id, "/api/v1/org/internal")
                if not org or str(org.get("_id")) != org_id:
                    return False
                org_payload = {"orgId": org_id, "accountType": org.get("accountType")}
                if org.get("registeredName"):
                    org_payload["registeredName"] = org["registeredName"]
                await self._handle_org_created(org_payload, bootstrap=True)
            user_payload = {"orgId": org_id, "userId": user_id, "email": user["email"], "fullName": user.get("fullName") or ""}
            if user.get("kind"):
                user_payload["kind"] = user["kind"]
            try:
                written = bool(await self._handle_user_added(user_payload, bootstrap=True))
            finally:
                await notify_principals_changed(org_id)  # as process_event does for a userAdded
            self.logger.info("Wrote the graph user of %s ahead of its userAdded event (written=%s)", user_id, written)
            return written
        except Exception as e:
            error_class.log_failure(self.logger, "entity_event.step_failed", e, step="ensure_user")
            return False

    async def _handle_user_added(self, payload: dict, *, bootstrap: bool = False) -> bool:
        """Handle user creation event.

        ``bootstrap``: written for the user's own first request (ensure_user), not by the event. It only creates a
        user where there is none, never touching one that exists: Node's answer is older than the write, and a user
        added and deleted meanwhile must stay deleted. Anything else is left to the events.
        """
        try:
            self.logger.debug("Processing user added event for user %s", payload.get("userId"))
            org_id = payload["orgId"]
            # The login user first, whatever its key (an address that changed must not leave it beside a new one);
            # then a connector's user of this address, which has no userId yet. raise_on_error: the providers
            # answer None to a failed read too, and "nobody" here would mint a second user for someone who is there.
            by_user_id = await self.graph_provider.get_user_by_user_id(payload["userId"], raise_on_error=True)
            user_key = (by_user_id.get("id") or by_user_id.get("_key")) if by_user_id else None
            if not user_key:
                existing_user = await self.graph_provider.get_user_by_email(
                    payload["email"], raise_on_error=True, org_id=org_id
                )
                user_key = existing_user.id if existing_user else None

            current_timestamp = get_epoch_timestamp_in_ms()
            if bootstrap and user_key:
                return False

            if user_key:
                user_data = {
                    "id": user_key,
                    "userId": payload["userId"],
                    "orgId": payload["orgId"],
                    "email": payload["email"],
                    "isActive": True,
                    "updatedAtTimestamp": current_timestamp,
                }
                # Set on the existing node too: a service account that is
                # restored, or recreated under an address the graph already
                # knows, arrives here rather than below. Left alone when
                # absent, so an older producer cannot blank a value a newer
                # one has already set. Nodes that predate the field are filled
                # in by the backfill, not from here.
                if payload.get("kind"):
                    user_data["kind"] = payload["kind"]
                # Sent by restore, which has just switched the account back on
                # in the collection that owns the answer. Left alone when
                # absent, so a producer that says nothing cannot change it.
                if payload.get("isDisabled") is not None:
                    user_data["isDisabled"] = bool(payload["isDisabled"])
            else:
                user_key = _login_user_key(payload["orgId"], payload["userId"])
                user_data = {
                    "id": user_key,
                    "userId": payload["userId"],
                    "orgId": payload["orgId"],
                    "email": payload["email"],
                    "fullName": payload.get("fullName", ""),
                    "firstName": payload.get("firstName", ""),
                    "middleName": payload.get("middleName", ""),
                    "lastName": payload.get("lastName", ""),
                    "designation": payload.get("designation", ""),
                    "businessPhones": payload.get("businessPhones", []),
                    "isActive": True,
                    # Absence means a person. Every producer of a service
                    # account sends this; nothing else needs to.
                    "kind": payload.get("kind") or "human",
                # Absence means not disabled, so only a true value is worth
                # storing; a person never carries this at all.
                **(
                    {"isDisabled": True}
                    if payload.get("isDisabled") is True
                    else {}
                ),
                    "createdAtTimestamp": current_timestamp,
                    "updatedAtTimestamp": current_timestamp,
                }

            # Get org details to check account type
            org = await self.graph_provider.get_document(
                org_id, CollectionNames.ORGS.value
            )
            if not org:
                self.logger.error(f"Organization not found: {org_id}")
                return False

            if bootstrap:
                if org.get("isActive") is False or not await self.graph_provider.create_node_if_absent(
                    user_key, CollectionNames.USERS.value, {k: v for k, v in user_data.items() if k != "id"}
                ):
                    return False
            else:
                await self.graph_provider.batch_upsert_nodes(
                    [user_data], CollectionNames.USERS.value
                )

            # Create edge between org and user if it doesn't exist
            edge_data = {
                "from_id": user_data["id"],
                "from_collection": CollectionNames.USERS.value,
                "to_id": payload["orgId"],
                "to_collection": CollectionNames.ORGS.value,
                "entityType": "ORGANIZATION",
                "createdAtTimestamp": current_timestamp,
            }
            await self.graph_provider.batch_create_edges(
                [edge_data],
                CollectionNames.BELONGS_TO.value,
            )

            # Adopt anything this email already accumulated as an external
            # collaborator. Never fatal: the account itself is what this event is for, and
            # letting a failed adoption bubble up would make Kafka redeliver forever while
            # the person still has no user.
            await self._adopt_existing_person(payload["email"], user_key, payload["orgId"])

            # Get or create knowledge base for the user (creates app + all edges)
            kb_name = self._kb_name_from_user_added_payload(payload)
            await self._get_or_create_knowledge_base(user_key, payload["userId"], payload["orgId"], name=kb_name)

            # Get or create "All" team for org and add user with PERMISSION edge
            await self._get_or_create_all_team_and_add_user(payload["orgId"], user_key)

            self.logger.info("Created or updated user %s", payload["userId"])
            self._schedule_connector_link(payload["orgId"])
            return True

        except SchemaValidationError:
            raise  # permanent: _dispatch logs it once and the event is not redelivered
        except Exception as e:
            error_class.log_failure(self.logger, "entity_event.failed", e, target_user_id=payload.get("userId"))
            raise

    def _schedule_connector_link(self, org_id: str) -> None:
        """Ask the org's running connectors that link users to do so, without holding up the event.

        At most one pass per connector is in flight; adds that arrive meanwhile are folded
        into one more pass that starts after the debounce.
        """
        for connector_id, connector in list(getattr(self.app_container, "connectors_map", {}).items()):
            if not getattr(connector, "user_link_steps", ()):
                continue
            connector_org = getattr(connector.data_entities_processor, "org_id", None)
            if not connector_org or connector_org != org_id:
                continue
            state = self._link_state.setdefault(connector_id, {"dirty": False, "task": None})
            state["dirty"] = True
            if state["task"] is None:
                # Detached: later passes serve other events, not the one that started them.
                state["task"] = org_context.spawn_detached(
                    self._link_passes(connector_id, connector_org), name="connector_link"
                )

    async def _link_passes(self, connector_id: str, org_id: str) -> None:
        state = self._link_state[connector_id]
        deferrals = 0
        try:
            with org_context.for_org(org_id, job="connector_link"), log_fields(connector_id=connector_id):
                while state["dirty"]:
                    await asyncio.sleep(_LINK_DEBOUNCE_SEC)
                    state["dirty"] = False
                    if await self._link_connector(connector_id):
                        deferrals = 0
                    elif deferrals < _LINK_MAX_DEFERRALS:
                        deferrals += 1
                        state["dirty"] = True
        finally:
            state["task"] = None

    async def _link_connector(self, connector_id: str) -> bool:
        """Best effort: a user added after a connector's last user sync has no edge to it
        until the next sync, so run the connector's own user-link steps (the source alone
        decides what the user may reach). Holds connector_sync_lock for the whole pass, so a
        sync that is admitted meanwhile waits for it, and is not run while the connector syncs
        (the caller retries once it has ended) nor for a connector that is off or being
        deleted. Returns False only when deferred; never raises.
        """
        try:
            connector = getattr(self.app_container, "connectors_map", {}).get(connector_id)
            if connector is None:
                return True
            lock = connector_sync_lock(connector_id)
            if lock.locked():
                return False
            async with lock:
                coordinator = get_coordinator()
                if coordinator is not None and await coordinator.is_running(connector_id):
                    return False
                app = await self.graph_provider.get_document(connector_id, CollectionNames.APPS.value)
                if not app or not app.get("isActive", False) or app.get("status") == APP_STATUS_DELETING:
                    return True
                # A reindex sees this pass as it sees a sync (`unless_synced_since`), since the pass writes access too: it moves a
                # stand-in group's grants onto the user it stood for, which a reindex that read the source earlier would take back.
                # The start moves the start time (seen by a reindex that began before). The mark, rewritten while the pass runs, is
                # what a reindex that begins meanwhile, in any process, takes for a running sync. It is not cleared at the end: a
                # pass in another process may share it, so it goes stale instead. A pass whose mark is not written does not run on.
                # A write gets a quarter of the TTL, as does the wait between writes: the next mark lands at most 3/4 of the
                # TTL after the last one's time, before it expires. A write that misses its deadline stops the pass.
                async def mark(*keys: str) -> None:
                    now = get_epoch_timestamp_in_ms()
                    ensure_written(await asyncio.wait_for(self.graph_provider.batch_upsert_nodes(
                        [{"id": connector_id, **dict.fromkeys(keys, now)}], CollectionNames.APPS.value,
                    ), USER_LINK_MARK_TTL_MS / 4000), "the user-link pass mark")

                note_sync_started(connector_id)
                await mark(ConnectorStateKeys.LAST_SYNC_STARTED_AT, ConnectorStateKeys.USER_LINK_STARTED_AT)
                link = org_context.spawn_in_request(connector.link_new_users(), name="connector_link_users")
                try:
                    while not (await asyncio.wait({link}, timeout=USER_LINK_MARK_TTL_MS / 4000))[0]:
                        await mark(ConnectorStateKeys.USER_LINK_STARTED_AT)
                    await link
                finally:
                    if not link.done():
                        link.cancel()
                        await asyncio.gather(link, return_exceptions=True)
        except Exception as e:
            error_class.log_failure(self.logger, "entity_event.step_failed", e, step="link_users")
        return True

    async def _handle_user_updated(self, payload: dict) -> bool:
        """Handle user update event"""
        try:
            self.logger.debug("Processing user updated event for user %s", payload.get("userId"))
            # raise_on_error: the providers answer None to a failed read too, and
            # "not found" would hide the outage from the classifier.
            existing_user = await self.graph_provider.get_user_by_user_id(
                payload["userId"], raise_on_error=True
            )

            if not existing_user:
                self.logger.error(f"User not found with userId: {payload['userId']}")
                return False

            user_id = existing_user.get("id") or existing_user.get("_key")
            user_data = {
                "id": user_id,
                "userId": payload["userId"],
                "orgId": payload["orgId"],
                "email": payload["email"],
                "fullName": payload.get("fullName", ""),
                "firstName": payload.get("firstName", ""),
                "middleName": payload.get("middleName", ""),
                "lastName": payload.get("lastName", ""),
                "designation": payload.get("designation", ""),
                "businessPhones": payload.get("businessPhones", []),

                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }

            # `isActive` is deliberately absent from the fields above. Soft
            # delete writes `isActive: False` (`_handle_user_deleted`) and
            # these upserts merge, so any `True` here would overwrite it. The
            # two handlers run at the same time: delete and update are
            # separate event types on one stream, the chart runs two connector
            # replicas in the same consumer group
            # (`app/services/messaging/utils.py:239`), and each can take one.
            # A `True` decided before a delete commits would land after it and
            # put a removed user back. The field belongs to the events that
            # own it — create, delete and restore. A rename is no evidence
            # that a user still exists.

            # The only signal the graph gets that an account was switched
            # off. Written only when the event carries it, so an update that
            # says nothing about it — one from a producer that predates the
            # field — cannot switch an account back on.
            if payload.get("isDisabled") is not None:
                user_data["isDisabled"] = bool(payload["isDisabled"])

            # Add only non-null optional fields
            optional_fields = [
                "fullName",
                "firstName",
                "middleName",
                "lastName",
                "email",
            ]
            user_data.update(
                {
                    key: payload[key]
                    for key in optional_fields
                    if payload.get(key) is not None
                }
            )

            # Batch upsert user
            await self.graph_provider.batch_upsert_nodes(
                [user_data], CollectionNames.USERS.value
            )

            # A node written before the field existed carries no `isActive`,
            # and the permission reads filter on `isActive == true`, so such a
            # node drops out of them. Stamped here rather than merged above
            # because the condition is evaluated inside the write: a delete
            # that lands first leaves `False` in place and this does nothing.
            # Skipped outright when the read already saw the field, which is
            # every account created since.
            if existing_user.get("isActive") is None:
                await self.graph_provider.update_node_fields_if_match(
                    user_id,
                    CollectionNames.USERS.value,
                    {"isActive": True},
                    {"isActive": None},
                )

            self.logger.info("Updated user %s", payload["userId"])
            return True

        except SchemaValidationError:
            raise  # permanent: _dispatch logs it once and the event is not redelivered
        except Exception as e:
            error_class.log_failure(self.logger, "entity_event.failed", e, target_user_id=payload.get("userId"))
            raise

    async def _handle_user_deleted(self, payload: dict) -> bool:
        """Handle user deletion event"""
        try:
            self.logger.debug("Processing user deleted event for user %s", payload.get("userId"))
            org_id = payload["orgId"]
            user_id = payload.get("userId")
            # The user the event names, by its userId first: the address may belong to a user added since (another userId, and
            # events of two users are not ordered). Then a user of the event's org with this address, never a group, a person,
            # another org's user or one that carries another userId. raise_on_error: a failed read must not read as "no such user".
            by_user_id = await self.graph_provider.get_user_by_user_id(user_id, raise_on_error=True) if user_id else None
            if by_user_id:      # a userId is one account, of one org (as userAdded resolves it)
                existing_user_id, email = by_user_id.get("id") or by_user_id.get("_key"), by_user_id.get("email") or payload["email"]
            else:
                existing_user = await self.graph_provider.get_user_by_email(payload["email"], raise_on_error=True, org_id=org_id)
                if existing_user is not None and user_id and existing_user.user_id not in (None, "", user_id):
                    existing_user = None
                existing_user_id, email = (existing_user.id if existing_user else None), payload["email"]
            if not existing_user_id:
                self.logger.error("No graph user is the deleted user (user %s)", user_id)
                return False

            user_data = {
                "id": existing_user_id,
                "orgId": org_id,
                "email": email,
                "isActive": False,
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }

            # Batch upsert user with isActive = False
            await self.graph_provider.batch_upsert_nodes(
                [user_data], CollectionNames.USERS.value
            )
            # At once, not after the cleanup below: until it moves, a scope read before the delete is still served.
            await notify_principals_changed(org_id)
            # Skills may be stamped under the graph key (REST) or the user id (run time).
            erased, reassigned = await erase_skill_author(
                self.graph_provider, org_id, [existing_user_id, payload.get("userId")],
            )
            self.logger.info("Soft-deleted graph user %s", existing_user_id)
            self.logger.info(
                "Skills of deleted graph user %s: erased=%s, passed to the org=%s", existing_user_id, erased, reassigned
            )
            if payload.get("userId"):
                await self._remove_mcp_data("user", payload["orgId"], payload["userId"])
                await self._remove_personal_toolsets("user", payload["orgId"], payload["userId"])
                await self._remove_api_toolset_data(payload["orgId"], payload["userId"])
            return True

        except SchemaValidationError:
            raise  # permanent: _dispatch logs it once and the event is not redelivered
        except Exception as e:
            error_class.log_failure(self.logger, "entity_event.failed", e, target_user_id=payload.get("userId"))
            raise

    async def _remove_mcp_data(self, owner_kind: str, org_id: str, user_id: str | None = None) -> None:
        """MCP servers and credentials go with their user or org, so nothing keeps refreshing
        their tokens. The graph change is already done, so a failure here is logged, not raised."""
        from app.agents.mcp import lifecycle as mcp_lifecycle

        try:
            config_service = self.app_container.config_service()
            if owner_kind == "org":
                removed = await mcp_lifecycle.remove_org_mcp_data(config_service, org_id)
            else:
                removed = await mcp_lifecycle.remove_user_mcp_data(config_service, org_id, user_id or "")
            self.logger.info(f"Removed {len(removed)} MCP keys for deleted {owner_kind} {user_id or org_id}")
        except Exception as e:
            error_class.log_failure(self.logger, "entity_event.step_failed", e, step="remove_mcp_data", target_user_id=user_id)

    async def _remove_personal_toolsets(self, owner_kind: str, org_id: str, user_id: str | None = None) -> None:
        """Personal toolset instances, with their sign-ins and tool rules, go with their user or org,
        so nothing keeps refreshing their tokens. Logged, not raised, as for MCP data."""
        from app.agents import toolset_store

        try:
            config_service = self.app_container.config_service()
            if owner_kind == "org":
                removed = await toolset_store.remove_org_personal_instances(config_service, org_id)
            else:
                removed = await toolset_store.remove_user_personal_instances(config_service, org_id, user_id or "")
            self.logger.info(f"Removed {removed} personal toolset instance(s) for deleted {owner_kind} {user_id or org_id}")
        except Exception as e:
            error_class.log_failure(self.logger, "entity_event.step_failed", e, step="remove_personal_toolsets", target_user_id=user_id)

    async def _remove_api_toolset_data(self, org_id: str, user_id: str | None = None) -> None:
        """A deleted org's API toolsets, or a deleted user's credentials for them. The graph
        change is already done, so a failure here is logged, not raised."""
        from app.agents.openapi.store import ApiToolsetStore

        try:
            store = ApiToolsetStore(self.app_container.config_service())
            if user_id:
                await store.remove_owner(org_id, user_id)
            else:
                await store.remove_org(org_id)
        except Exception as e:
            error_class.log_failure(self.logger, "entity_event.step_failed", e, step="remove_api_toolset_data", target_user_id=user_id)

    # APP EVENTS
    async def _handle_app_enabled(self, payload: dict) -> bool:
        """Handle app enabled event"""
        try:
            org_id = payload["orgId"]
            apps = payload["apps"]
            sync_action = payload.get("syncAction", "none")
            connector_id = payload.get("connectorId", "")
            scope = payload.get("scope", ConnectorScopes.PERSONAL.value)
            full_sync = payload.get("fullSync", False)
            synced_by = payload.get("syncedBy", "")
            # Get org details to check account type
            org = await self.graph_provider.get_document(
                org_id, CollectionNames.ORGS.value
            )
            if not org:
                self.logger.error(f"Organization not found: {org_id}")
                return False

            if connector_id and payload.get(ConnectorStateKeys.TOGGLE_SEQ) is not None:
                app_doc = await self.graph_provider.get_document(
                    connector_id, CollectionNames.APPS.value
                )
                if app_doc and _enable_superseded_by_a_newer_toggle(payload, app_doc):
                    self.logger.info(
                        f"Skipping app enabled event for connector {connector_id}: a newer toggle "
                        f"(#{app_doc.get(ConnectorStateKeys.TOGGLE_SEQ)}) was written after this one "
                        f"(#{payload.get(ConnectorStateKeys.TOGGLE_SEQ)})"
                    )
                    return True

            for app_name in apps:
                if sync_action == "immediate":
                    # Start sync for each app (connector already initialized for standard connectors)
                    await self._handle_sync_event(
                        event_type=f"{app_name.lower()}.start",
                        value={
                            "orgId": org_id,
                            "connector": app_name,
                            "connectorId": connector_id,
                            "scope": scope,
                            "fullSync": full_sync,
                            "syncedBy": synced_by,
                        },
                    )

            if connector_id:
                await self._start_requeue_of_parked_records(connector_id)

            self.logger.info(f"Successfully enabled apps for org: {org_id}")
            return True

        except SchemaValidationError:
            raise  # permanent: _dispatch logs it once and the event is not redelivered
        except Exception as e:
            error_class.log_failure(self.logger, "entity_event.failed", e)
            raise

    async def _write_app_disabled(self, payload: dict, app_doc: dict) -> bool | None:
        """Write isActive false, unless a newer toggle turned the connector back on.

        Returns True when written, False when superseded, None when the app went away. A
        numbered event writes only while the app still has the number it was checked
        against, so an on that commits between the read and the write is seen, not
        overwritten; an event without a number writes as it always has.
        """
        connector_id = payload.get("connectorId", "")
        apps = CollectionNames.APPS.value
        disabled = {"isActive": False, "updatedAtTimestamp": get_epoch_timestamp_in_ms()}
        if payload.get(ConnectorStateKeys.TOGGLE_SEQ) is None or app_doc.get(ConnectorStateKeys.TOGGLE_SEQ) is None:
            await self.graph_provider.update_node(connector_id, apps, disabled)
            return True
        for _ in range(_DISABLE_WRITE_ATTEMPTS):
            if _superseded_by_a_newer_toggle(payload, app_doc):
                # A quick off-then-on can reach here after the on was written: disabling now
                # would turn the connector off again and skip the sync the on started.
                self.logger.info(
                    f"Skipping app disabled event for connector {connector_id}: a newer toggle "
                    f"(#{app_doc.get(ConnectorStateKeys.TOGGLE_SEQ)}) turned it back on after this one "
                    f"(#{payload.get(ConnectorStateKeys.TOGGLE_SEQ)})"
                )
                return False
            if await self.graph_provider.update_node_fields_if_match(
                connector_id, apps, disabled,
                {ConnectorStateKeys.TOGGLE_SEQ: app_doc[ConnectorStateKeys.TOGGLE_SEQ]},
            ):
                return True
            app_doc = await self.graph_provider.get_document(connector_id, apps)
            if not app_doc:
                self.logger.error(f"App not found: {connector_id}")
                return None
        raise RuntimeError(
            f"Connector {connector_id} was toggled {_DISABLE_WRITE_ATTEMPTS} times while its "
            "app disabled event was being handled"
        )

    async def _handle_app_disabled(self, payload: dict) -> bool:
        """Handle app disabled event"""
        try:
            org_id = payload["orgId"]
            apps = payload["apps"]
            connector_id = payload.get("connectorId", "")

            if not org_id or not apps:
                self.logger.error("Both orgId and apps are required to disable apps")
                return False

            # Stop sync for each app

            # Set apps as inactive
            for app_name in apps:
                app_doc = await self.graph_provider.get_document(
                    connector_id, CollectionNames.APPS.value
                )
                if not app_doc:
                    self.logger.error(f"App not found: {app_name}")
                    return False

            # Update apps in database. Only the two fields that change: rebuilding the
            # whole document from the stored one would write None for a field a legacy
            # app lacks, which the schema (rightly) rejects.
            disabled = await self._write_app_disabled(payload, app_doc)
            if disabled is None:
                return False
            if not disabled:
                return True
            await notify_principals_changed(org_id)  # before the (slow) cleanup below; see process_event

            # Stop any running sync/reindex and wait for it to unwind, bounded:
            # the sweep and the cleanup below must not run while the sync is
            # still writing records (they would be left QUEUED with nothing to
            # pick them up), but this is the serial entity consumer, so an
            # unbounded wait would stall every event behind it.
            try:
                reindex_task_manager.request_stop_by_prefix(f"reindex:{connector_id}:")
                coordinator = get_coordinator()
                if coordinator is not None:
                    await coordinator.request_stop(connector_id)
                if await self._wait_for_sync_to_stop(connector_id):
                    self.logger.info(f"Stopped running sync/reindex for connector {connector_id}")
                else:
                    self.logger.warning(
                        f"Sync/reindex for connector {connector_id} still unwinding after "
                        f"{stop_wait_sec(self.logger)}s; disabling anyway"
                    )
            except Exception as cancel_err:
                error_class.log_failure(self.logger, "entity_event.step_failed", cancel_err, step="stop_sync")

            # A connector parked at the concurrency limit is owed a sync that must
            # no longer run: without this it shows QUEUED until re-enabled.
            try:
                app_doc = await self.graph_provider.get_document(
                    connector_id, CollectionNames.APPS.value
                )
                if app_doc and app_doc.get("status") == AppStatus.QUEUED.value:
                    await self.graph_provider.update_node(
                        connector_id,
                        CollectionNames.APPS.value,
                        {
                            "status": AppStatus.IDLE.value,
                            ConnectorStateKeys.PENDING_RESYNC: False,
                            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                        },
                    )
            except Exception as queue_err:
                error_class.log_failure(self.logger, "entity_event.step_failed", queue_err, step="clear_queued_sync")

            # Drain the backlog. Records already QUEUED have no event guard to
            # catch them and no processingStartedAt, so stale recovery — which
            # only scans IN_PROGRESS — can never reach them: they would sit in
            # QUEUED for ever. Run this *after* cancelling the sync so nothing
            # re-queues behind the sweep. IN_PROGRESS is excluded deliberately;
            # those are mid-pipeline and are handled by stale recovery.
            # Only QUEUED, and stamped CONNECTOR_OFF like every other turn-off
            # path: that reason is what lets turning the connector on queue
            # them again, so it must not land on records the connector's
            # filters keep out of auto-indexing, or on FAILED/EMPTY outcomes.
            # Not on trashed ones either: the resume skips the trash, so one
            # restored afterwards would stay parked with the connector on.
            try:
                await self.graph_provider.reset_indexing_status_for_connector(
                    connector_id,
                    ProgressStatus.AUTO_INDEX_OFF.value,
                    only_statuses=[ProgressStatus.QUEUED.value],
                    reason=CONNECTOR_OFF,
                    visibility=RecordVisibility.LIVE,
                )
                self.logger.info(
                    f"Moved queued records for connector {connector_id} to manual indexing"
                )
            except Exception as sweep_err:
                error_class.log_failure(self.logger, "entity_event.step_failed", sweep_err, step="park_queued_records")

            await evict_cached_connector(self.app_container, connector_id, self.logger)

            self.logger.info(f"Successfully disabled apps for org: {org_id}")
            return True

        except SchemaValidationError:
            raise  # permanent: _dispatch logs it once and the event is not redelivered
        except Exception as e:
            error_class.log_failure(self.logger, "entity_event.failed", e)
            raise

    async def _adopt_existing_person(self, email: str, user_key: str, org_id: str) -> None:
        """Move a pre-existing Person's collaborator edges onto the new user.

        Someone shared files with this address before its owner had an account; those
        grants live on a Person node and must follow them in, or they sign up and see
        nothing. A Person that is also a Salesforce contact splits instead of merging -
        see docs/external-user-support-plan.md, D4.
        """
        try:
            mode = await self.graph_provider.migrate_person_to_user(email, user_key, org_id)
            if mode:
                self.logger.info("Adopted existing person for user %s (%s)", user_key, mode)
        except Exception as e:
            self.logger.error("Failed to adopt existing person for user %s: %s", user_key, e, exc_info=True)

    async def _start_requeue_of_parked_records(self, connector_id: str) -> None:
        """Queue again, in the background, what was parked while the connector was off.

        A background task because a long backlog would hold this serial
        consumer. The ``reindex:<id>:`` key puts it under the stop that
        turning the connector off already issues for that prefix.
        """
        try:
            await reindex_task_manager.start_if_idle(
                f"reindex:{connector_id}:parked-while-off",
                self._requeue_records_parked_while_off(connector_id),
            )
        except Exception as e:
            # Never fail the enable over it; the next one tries again.
            error_class.log_failure(self.logger, "entity_event.step_failed", e, step="requeue_parked_records")

    async def _requeue_records_parked_while_off(self, connector_id: str) -> int:
        """Re-publish the connector's records parked as AUTO_INDEX_OFF / CONNECTOR_OFF.

        Every turn-off path parks queued and in-flight records that way, and
        their message tells the user turning the connector on will index them.
        A sync does not: an unchanged record is not sent again. Returns how
        many were re-published.

        Published first, then moved to QUEUED only while still parked, as
        ``_mark_queued_after_publish`` does: the indexing service may already
        have taken the record on, and that must not be overwritten.

        Pages with a key cursor, not an offset: the result set changes under
        the walk (the sync started beside this deletes records, trash purge
        removes them, a write may not take), and an offset would then skip a
        record or send one twice. The cursor visits each record at most once.
        """
        connector = await self.graph_provider.get_document(
            connector_id, CollectionNames.APPS.value, raise_on_error=True
        )
        if not connector or not connector.get("isActive", False):
            # A stale enable, or turned off again since: it would only be parked again.
            return 0

        producer = self.app_container.messaging_producer
        filters = {"connectorId": connector_id, **_PARKED_WHILE_OFF}
        requeued = 0
        # "" rather than None, so the first page has the key predicate too:
        # Neo4j uses the (connectorId, indexingStatus, reason, id) index only
        # when every property in it has one.
        after_key = ""
        while True:
            page = await self.graph_provider.get_documents_paginated(
                CollectionNames.RECORDS.value,
                limit=_REQUEUE_PAGE_SIZE,
                filters=filters,
                sort_field="_key",
                raise_on_error=True,
                after_key=after_key,
            )
            if not page:
                break
            # Advanced before any work, so nothing on this page is read again.
            after_key = page[-1].get("_key") or page[-1].get("id")
            if not after_key:
                self.logger.error(
                    f"Last parked record of connector {connector_id} has no key; "
                    "stopping the re-queue rather than reading the page again"
                )
                break

            candidates = [
                (key, record)
                for record in page
                if (key := record.get("_key") or record.get("id"))
                and record.get("origin") == OriginTypes.CONNECTOR.value
                and is_live_record(record)
            ]
            now = get_epoch_timestamp_in_ms()
            messages = []
            for key, record in candidates:
                event_type, payload = record_event(
                    record, record_key=key, connector_id=connector_id
                )
                messages.append(
                    (key, {"eventType": event_type, "timestamp": now, "payload": payload})
                )
            acked = (
                await producer.send_messages("record-events", messages)
                if messages
                else []
            )
            sent = [pair for pair, ok in zip(candidates, acked) if ok]
            if sent:
                await self.graph_provider.update_nodes_fields_if_match(
                    CollectionNames.RECORDS.value,
                    [
                        (key, self._requeued_fields(record, now), dict(_PARKED_WHILE_OFF))
                        for key, record in sent
                    ],
                )
            requeued += len(sent)

            if len(page) < _REQUEUE_PAGE_SIZE:
                break

        if requeued:
            self.logger.info(
                f"Re-queued {requeued} record(s) parked while connector {connector_id} was off"
            )
        return requeued

    @staticmethod
    def _requeued_fields(record: dict, now: int) -> dict:
        fields: dict = {
            "indexingStatus": ProgressStatus.QUEUED.value,
            "queuedAtTimestamp": now,
            "processingStartedAt": None,
            "reason": None,
            # a record parked while its connector was off has no open run: this starts one
            **run_reference(record, WriteKind.QUEUE, now),
        }
        # Stale recovery parks these too when it parks an in-flight record.
        for status_field in ("parsingStatus", "extractionStatus"):
            if record.get(status_field) == ProgressStatus.AUTO_INDEX_OFF.value:
                fields[status_field] = ProgressStatus.NOT_STARTED.value
        return fields

    async def _wait_for_sync_to_stop(self, connector_id: str) -> bool:
        """Whether the connector's sync and reindex tasks ended within the wait."""
        coordinator = get_coordinator()
        prefix = f"reindex:{connector_id}:"
        deadline = time.monotonic() + stop_wait_sec(self.logger)
        while True:
            busy = (
                coordinator is not None and await coordinator.is_running(connector_id)
            ) or any(k.startswith(prefix) for k in reindex_task_manager.active_keys())
            if not busy:
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.25)

    def _kb_name_from_user_added_payload(self, payload: dict) -> str:
        """Compute KB display name from userAdded event: fullName's Private or email's Private."""
        full_name = (payload.get("fullName") or "").strip()
        if full_name:
            return f"{full_name}'s Private"
        email = (payload.get("email") or "").strip()
        if email:
            return f"{email}'s Private"
        return "Private"

    async def _create_all_team_for_org(self, org_id: str, created_by_user_id: str | None = None) -> None:
        """
        Create the "All" team when an org is created. Called from _handle_org_created.
        created_by_user_id is the external userId (e.g. MongoDB id); graph user key is set when first user is added.
        """
        try:
            current_timestamp = get_epoch_timestamp_in_ms()
            team_key = f"all_{org_id}"
            created_by = created_by_user_id if created_by_user_id else "system"
            team_node = {
                "id": team_key,
                "name": "All",
                "description": "All organization members",
                "createdBy": created_by,
                "orgId": org_id,
                "createdAtTimestamp": current_timestamp,
                "updatedAtTimestamp": current_timestamp,
            }
            await self.graph_provider.batch_upsert_nodes(
                [team_node], CollectionNames.TEAMS.value
            )
            self.logger.info(f"Created 'All' team for org {org_id}")
        except Exception as e:
            error_class.log_failure(self.logger, "entity_event.step_failed", e, step="create_all_team")

    async def _get_or_create_all_team_and_add_user(self, org_id: str, user_key: str) -> None:
        """
        Add the specific user to the org's "All" team.
        Ensures team exists and creates PERMISSION edge for this user only.
        """
        try:
            await self.graph_provider.add_user_to_all_team(org_id, user_key)
            self.logger.info(f"Added user {user_key} to 'All' team for org {org_id}")
        except Exception as e:
            self.logger.error(
                f"Failed to add user {user_key} to 'All' team for org {org_id}: {str(e)}",
                exc_info=True
            )

    @staticmethod
    async def _ensure_kb_edges(tx_store: "TransactionStore", user_key: str, org_id: str, kb_key: str) -> None:
        """The three edges that make a default knowledge base reachable, written create-only.

        No read decides this: both providers answer None to a failed edge read as
        well as to a missing edge, and a replacing write after that would reset a
        live edge's sync state or role. An edge that is there is left as it is.
        """
        timestamp = get_epoch_timestamp_in_ms()
        await tx_store.create_edges_if_absent([{
            "from_id": user_key,
            "from_collection": CollectionNames.USERS.value,
            "to_id": kb_key,
            "to_collection": CollectionNames.APPS.value,
            "externalPermissionId": "",
            "type": "USER",
            "role": "OWNER",
            "createdAtTimestamp": timestamp,
            "updatedAtTimestamp": timestamp,
            "lastUpdatedTimestampAtSource": timestamp,
        }], CollectionNames.PERMISSION.value)
        await tx_store.create_edges_if_absent([{
            "from_id": org_id,
            "from_collection": CollectionNames.ORGS.value,
            "to_id": kb_key,
            "to_collection": CollectionNames.APPS.value,
            "createdAtTimestamp": timestamp,
        }], CollectionNames.ORG_APP_RELATION.value)
        await tx_store.ensure_app_membership(user_key, CollectionNames.USERS.value, kb_key, is_external=False)

    async def _get_or_create_knowledge_base(
        self,
        user_key: str,
        userId: str,
        orgId: str,
        name: str = "Private"
    ) -> dict:
        """Get or create a default knowledge base app for a user.

        Raises when the graph write fails, so the user event is not acknowledged
        with the knowledge base missing or incomplete.
        """
        if not userId or not orgId:
            self.logger.error("Both User ID and Organization ID are required to get or create a knowledge base")
            return {}

        # The user names their default on their own node (defaultKnowledgeBaseId), and the name is set in the very
        # transaction that writes the knowledge base, on the condition that it still says what was read. So a
        # knowledge base they made themselves, possibly in the moment between their user and their default being
        # written, never stands in for it; of two writers of one user's default exactly one commits and the other
        # then finds it; a write that fails leaves no claim behind for a retry to trip on; and no key is ever written
        # twice: a named default that is gone is replaced under a fresh key, which cleanup queued for the deleted one,
        # keyed by its id, cannot reach.
        for _ in range(_DEFAULT_KB_CLAIM_ATTEMPTS):
            # The name before the knowledge bases: both commit together, so a name read first has its knowledge base
            # visible to the read after it. The other way round, a default committed between the two reads would
            # look named but missing, and be replaced.
            user_doc = await self.graph_provider.get_document(user_key, CollectionNames.USERS.value, raise_on_error=True)
            marker = (user_doc or {}).get("defaultKnowledgeBaseId")
            marker = marker if isinstance(marker, str) and marker else None
            # raise_on_error: the providers answer [] to a failed lookup too, and "no
            # knowledge base" here would mint a second App under a new id.
            all_kbs = await self.graph_provider.get_nodes_by_filters(
                collection=CollectionNames.APPS.value,
                filters={
                    "createdBy": userId,
                    "orgId": orgId,
                    "type": Connectors.KNOWLEDGE_BASE.value,
                },
                raise_on_error=True,
            )
            default = next((kb for kb in all_kbs if (kb.get("id") or kb.get("_key")) == marker), None) if marker else None
            live = default is not None and not default.get("isDeleted", False) and default.get("status") != APP_STATUS_DELETING

            if live:
                existing_kbs = [default]
            elif marker is None and user_key != _login_user_key(orgId, userId):
                # A user under an older key whose default predates the name: any knowledge base of theirs will do.
                existing_kbs = [kb for kb in all_kbs if not kb.get("isDeleted", False)]
            else:
                existing_kbs = []

            if existing_kbs:
                self.logger.info(f"Found existing KB app for user {userId} in organization {orgId}")
                # A create that failed partway on Neo4j before creates ran in a real
                # transaction (or with NEO4J_CREATE_TRANSACTIONS=false) left the App
                # without some of its edges; finish it rather than hand it out unusable
                # again. Create-only, so a complete one is untouched.
                # Every match, not the first: the lookup is unordered.
                for existing in existing_kbs:
                    existing_key = existing.get("id") or existing.get("_key")
                    if existing_key:
                        await self.graph_data_store.execute_idempotent_in_transaction(
                            self._ensure_kb_edges, user_key, orgId, existing_key, explicit=True
                        )
                return existing_kbs[0]

            current_timestamp = get_epoch_timestamp_in_ms()
            kb_key = str(uuid4())
            kb_data = {
                "id": kb_key,
                "createdBy": userId,
                "orgId": orgId,
                "name": name,
                "type": Connectors.KNOWLEDGE_BASE.value,
                "appGroup": AppGroups.LOCAL_STORAGE.value,
                "authType": "NONE",
                "scope": ConnectorScopes.PERSONAL.value,
                "isActive": True,
                "isAgentActive": True,
                "isConfigured": True,
                "isAuthenticated": True,
                "vectorMembershipBackfilled": True,
                "hideConnector": True,
                "createdAtTimestamp": current_timestamp,
                "updatedAtTimestamp": current_timestamp,
            }

            async def write_kb(tx_store: "TransactionStore", kb_key: str = kb_key, kb_data: dict = kb_data,
                               marker: str | None = marker) -> None:
                if not await tx_store.name_default_knowledge_base(user_key, kb_key, marker):
                    raise _DefaultKnowledgeBaseNamedMeanwhile
                ensure_written(
                    await tx_store.batch_upsert_nodes([kb_data], CollectionNames.APPS.value), "the default knowledge base",
                )
                await self._ensure_kb_edges(tx_store, user_key, orgId, kb_key)

            try:
                # explicit: a failure leaves no App without its edges, and no name without its App, on Neo4j either.
                # Every write is keyed by kb_key, so a re-run after a write conflict (the org node is shared by every
                # onboarding message) completes the same App rather than starting a second one.
                await self.graph_data_store.execute_idempotent_in_transaction(write_kb, explicit=True)
                break
            except _DefaultKnowledgeBaseNamedMeanwhile:
                continue  # another writer committed the user's default: read again and find it
        else:
            raise RuntimeError(f"could not name a default knowledge base for user {user_key}")

        # Register per-KB connector instance at runtime
        try:
            config_service = self.app_container.config_service()
            data_store_provider = await self.app_container.data_store()
            if not hasattr(self.app_container, 'connectors_map'):
                self.app_container.connectors_map = {}
            # Two writers of one user both reach here with the same kb_key: one instance is built.
            async with connector_init_lock(kb_key):
                if kb_key not in self.app_container.connectors_map:
                    connector = await ConnectorFactory.create_and_start_sync(
                        name="kb",
                        logger=self.logger,
                        data_store_provider=data_store_provider,
                        config_service=config_service,
                        connector_id=kb_key,
                        scope=ConnectorScopes.PERSONAL.value,
                        created_by=userId,
                        org_id=orgId,
                        data_entities_processor_cls=get_data_entities_processor_cls(),
                        notification_service=self.app_container.connector_notification_service(),
                    )
                    if connector:
                        self.app_container.connectors_map[kb_key] = connector
                        self.logger.info(f"KB connector instance registered for kb_key={kb_key}")
        except Exception as reg_err:
            error_class.log_failure(
                self.logger, "entity_event.step_failed", reg_err, step="register_kb_connector", connector_id=kb_key
            )

        self.logger.info(f"Created new KB app for user {userId} in organization {orgId} (kb_key={kb_key})")
        return {
            "kb_id": kb_key,
            "name": name,
            "created_at": current_timestamp,
            "updated_at": current_timestamp,
            "success": True
        }
