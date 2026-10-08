import asyncio
import logging
import time
from typing import TYPE_CHECKING
from uuid import uuid4

from app.config.constants.arangodb import (
    AccountType,
    AppGroups,
    AppStatus,
    CollectionNames,
    Connectors,
    ConnectorScopes,
    ProgressStatus,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.core.base.event_service.event_service import BaseEventService
from app.connectors.core.constants import ConnectorStateKeys
from app.connectors.core.factory.connector_factory import ConnectorFactory
from app.connectors.core.sync.sync_coordinator import get_coordinator, stop_wait_sec
from app.connectors.core.sync.task_manager import reindex_task_manager
from app.containers.connector import (
    ConnectorAppContainer,
)
from app.edition_services import get_data_entities_processor_cls
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.connectors.core.base.data_store.data_store import TransactionStore


class EntityEventService(BaseEventService):
    def __init__(
        self,
        logger: logging.Logger,
        graph_provider: IGraphDBProvider,
        app_container: ConnectorAppContainer,
    ) -> None:
        self.logger = logger
        self.graph_provider = graph_provider
        self.graph_data_store = GraphDataStore(logger, graph_provider)
        self.app_container = app_container

    async def process_event(self, event_type: str, payload: dict) -> bool:
        """Handle entity-related events by calling appropriate handlers"""
        try:
            self.logger.info(f"Processing entity event: {event_type}")
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
            else:
                self.logger.error(f"Unknown entity event type: {event_type}")
                return False
        except Exception as e:
            self.logger.error(f"Error processing entity event: {str(e)}")
            return False

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
            self.logger.error(f"Error sending sync event: {str(e)}")
            return False

    # ORG EVENTS
    async def _handle_org_created(self, payload: dict) -> bool:
        """Handle organization creation event"""

        accountType = (
            AccountType.ENTERPRISE.value
            if payload["accountType"] in [AccountType.BUSINESS.value, AccountType.ENTERPRISE.value]
            else AccountType.INDIVIDUAL.value
        )
        try:
            org_data = {
                "_key": payload["orgId"],
                "name": payload.get("registeredName", "Individual Account"),
                "accountType": accountType,
                "isActive": True,
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }

            # Convert _key to id for provider
            org_data["id"] = org_data.pop("_key")

            # Batch upsert org
            await self.graph_provider.batch_upsert_nodes(
                [org_data], CollectionNames.ORGS.value
            )

            # Get departments with orgId == None using provider
            departments = await self.graph_provider.get_nodes_by_filters(
                collection=CollectionNames.DEPARTMENTS.value,
                filters={"orgId": None}
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
                    f"✅ Successfully created organization: {payload['orgId']} and relationships with departments"
                )
            else:
                self.logger.info(
                    f"✅ Successfully created organization: {payload['orgId']}"
                )

            # Create "All" team for the org (first user will be added with OWNER in userAdded)
            await self._create_all_team_for_org(payload['orgId'], payload.get('userId'))

            return True

        except Exception as e:
            self.logger.error(f"❌ Error creating organization: {str(e)}")
            return False

    async def _handle_org_updated(self, payload: dict) -> bool:
        """Handle organization update event"""
        try:
            self.logger.info(f"📥 Processing org updated event: {payload}")
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
                f"✅ Successfully updated organization: {payload['orgId']}"
            )
            return True

        except Exception as e:
            self.logger.error(f"❌ Error updating organization: {str(e)}")
            return False

    async def _handle_org_deleted(self, payload: dict) -> bool:
        """Handle organization deletion event"""
        try:
            self.logger.info(f"📥 Processing org deleted event: {payload}")
            org_data = {
                "_key": payload["orgId"],
                "isActive": False,
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }

            # Convert _key to id for provider
            org_data["id"] = org_data.pop("_key")

            # Batch upsert org with isActive = False
            await self.graph_provider.batch_upsert_nodes(
                [org_data], CollectionNames.ORGS.value
            )
            self.logger.info(
                f"✅ Successfully soft-deleted organization: {payload['orgId']}"
            )
            return True

        except Exception as e:
            self.logger.error(f"❌ Error deleting organization: {str(e)}")
            return False

    # USER EVENTS
    async def _handle_user_added(self, payload: dict) -> bool:
        """Handle user creation event"""
        try:
            self.logger.info(f"📥 Processing user added event: {payload}")
            # Check if user already exists by email
            existing_user = await self.graph_provider.get_user_by_email(
                payload["email"]
            )

            current_timestamp = get_epoch_timestamp_in_ms()

            if existing_user:
                # existing_user is a User object, get id from it
                user_key = existing_user.id
                user_data = {
                    "id": user_key,
                    "userId": payload["userId"],
                    "orgId": payload["orgId"],
                    "isActive": True,
                    "updatedAtTimestamp": current_timestamp,
                }
            else:
                user_key = str(uuid4())
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
                    "createdAtTimestamp": current_timestamp,
                    "updatedAtTimestamp": current_timestamp,
                }

            # Get org details to check account type
            org_id = payload["orgId"]
            org = await self.graph_provider.get_document(
                org_id, CollectionNames.ORGS.value
            )
            if not org:
                self.logger.error(f"Organization not found: {org_id}")
                return False

            # Batch upsert user
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

            self.logger.info(
                f"✅ Successfully created/updated user: {payload['email']}"
            )
            return True

        except Exception as e:
            self.logger.error(f"❌ Error creating/updating user: {str(e)}")
            return False

    async def _handle_user_updated(self, payload: dict) -> bool:
        """Handle user update event"""
        try:
            self.logger.info(f"📥 Processing user updated event: {payload}")
            # Find existing user by userId
            existing_user = await self.graph_provider.get_user_by_user_id(
                payload["userId"],
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

                "isActive": True,
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }

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
            self.logger.info(f"✅ Successfully updated user: {payload['email']}")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error updating user: {str(e)}")
            return False

    async def _handle_user_deleted(self, payload: dict) -> bool:
        """Handle user deletion event"""
        try:
            self.logger.info(f"📥 Processing user deleted event: {payload}")
            # Find existing user by email
            existing_user_id = await self.graph_provider.get_entity_id_by_email(
                payload["email"]
            )
            if not existing_user_id:
                self.logger.error(f"User not found with mail: {payload['email']}")
                return False

            user_data = {
                "id": existing_user_id,
                "orgId": payload["orgId"],
                "email": payload["email"],
                "isActive": False,
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }

            # Batch upsert user with isActive = False
            await self.graph_provider.batch_upsert_nodes(
                [user_data], CollectionNames.USERS.value
            )
            self.logger.info(f"✅ Successfully soft-deleted user: {payload['email']}")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error deleting user: {str(e)}")
            return False

    # APP EVENTS
    async def _handle_app_enabled(self, payload: dict) -> bool:
        """Handle app enabled event"""
        try:
            self.logger.info(f"📥 Processing app enabled event: {payload}")
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

            self.logger.info(f"✅ Successfully enabled apps for org: {org_id}")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error enabling apps: {str(e)}")
            return False

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
            self.logger.info(f"📥 Processing app disabled event: {payload}")

            # Set apps as inactive
            app_updates = []
            for app_name in apps:
                app_doc = await self.graph_provider.get_document(
                    connector_id, CollectionNames.APPS.value
                )
                if not app_doc:
                    self.logger.error(f"App not found: {app_name}")
                    return False
                app_data = {
                    "id": connector_id,
                    "name": app_doc.get("name", app_doc.get("_key", connector_id)),
                    "type": app_doc.get("type"),
                    "appGroup": app_doc.get("appGroup"),
                    "isActive": False,
                    "createdAtTimestamp": app_doc.get("createdAtTimestamp"),
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                }
                app_updates.append(app_data)

            # Update apps in database
            await self.graph_provider.batch_upsert_nodes(
                app_updates, CollectionNames.APPS.value
            )

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
                    self.logger.info(f"✅ Stopped running sync/reindex for connector {connector_id}")
                else:
                    self.logger.warning(
                        f"Sync/reindex for connector {connector_id} still unwinding after "
                        f"{stop_wait_sec(self.logger)}s; disabling anyway"
                    )
            except Exception as cancel_err:
                self.logger.error(f"❌ Failed to stop sync for connector {connector_id}: {cancel_err}")

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
                self.logger.error(
                    f"❌ Failed to clear the queued sync of disabled connector {connector_id}: {queue_err}"
                )

            # Drain the backlog. Records already QUEUED have no event guard to
            # catch them and no processingStartedAt, so stale recovery — which
            # only scans IN_PROGRESS — can never reach them: they would sit in
            # QUEUED for ever. Run this *after* cancelling the sync so nothing
            # re-queues behind the sweep. IN_PROGRESS is excluded deliberately;
            # those are mid-pipeline and are handled by stale recovery.
            try:
                await self.graph_provider.reset_indexing_status_for_connector(
                    connector_id,
                    ProgressStatus.AUTO_INDEX_OFF.value,
                    exclude_statuses=[
                        ProgressStatus.IN_PROGRESS.value,
                        ProgressStatus.COMPLETED.value,
                    ],
                )
                self.logger.info(
                    f"✅ Moved queued records for connector {connector_id} to manual indexing"
                )
            except Exception as sweep_err:
                self.logger.error(
                    f"❌ Failed to move queued records to manual indexing for "
                    f"connector {connector_id}: {sweep_err}"
                )

            if (
                hasattr(self.app_container, "connectors_map")
                and connector_id in self.app_container.connectors_map
            ):
                existing_connector = self.app_container.connectors_map.pop(connector_id)
                try:
                    if hasattr(existing_connector, "cleanup"):
                        await existing_connector.cleanup()
                    self.logger.info(f"Cleaned up connector instance {connector_id}")
                except Exception as cleanup_err:
                    self.logger.error(f"Error cleaning up connector {connector_id}: {cleanup_err}")

            self.logger.info(f"✅ Successfully disabled apps for org: {org_id}")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error disabling apps: {str(e)}")
            return False

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
                self.logger.info(
                    f"✅ Adopted existing person for {email} ({mode})"
                )
        except Exception as e:
            self.logger.error(
                f"❌ Failed to adopt existing person for {email}: {str(e)}",
                exc_info=True,
            )

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
            self.logger.error(f"Failed to create 'All' team for org {org_id}: {str(e)}", exc_info=True)

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

        # Check if a KB app already exists for this user in this organization
        existing_kbs = await self.graph_provider.get_nodes_by_filters(
            collection=CollectionNames.APPS.value,
            filters={
                "createdBy": userId,
                "orgId": orgId,
                "type": Connectors.KNOWLEDGE_BASE.value,
            }
        )
        existing_kbs = [kb for kb in existing_kbs if not kb.get("isDeleted", False)]

        if existing_kbs:
            existing = existing_kbs[0]
            existing_key = existing.get("id") or existing.get("_key")
            self.logger.info(f"Found existing KB app for user {userId} in organization {orgId}")
            # A create that failed partway on Neo4j (each statement commits on its
            # own) left the App without some of its edges; finish it rather than
            # hand it out unusable again. Create-only, so a complete one is untouched.
            if existing_key:
                await self.graph_data_store.execute_idempotent_in_transaction(
                    self._ensure_kb_edges, user_key, orgId, existing_key
                )
            return existing

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

        async def write_kb(tx_store: "TransactionStore") -> None:
            await tx_store.batch_upsert_nodes([kb_data], CollectionNames.APPS.value)
            await self._ensure_kb_edges(tx_store, user_key, orgId, kb_key)

        # Every write is keyed by kb_key, so a re-run after a write conflict
        # (the org node is shared by every onboarding message) completes the
        # same App rather than starting a second one.
        await self.graph_data_store.execute_idempotent_in_transaction(write_kb)

        # Register per-KB connector instance at runtime
        try:
            config_service = self.app_container.config_service()
            data_store_provider = await self.app_container.data_store()
            if not hasattr(self.app_container, 'connectors_map'):
                self.app_container.connectors_map = {}
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
                self.logger.info(f"✅ KB connector instance registered for kb_key={kb_key}")
        except Exception as reg_err:
            self.logger.warning(f"⚠️ Failed to register KB connector instance: {reg_err}")

        self.logger.info(f"Created new KB app for user {userId} in organization {orgId} (kb_key={kb_key})")
        return {
            "kb_id": kb_key,
            "name": name,
            "created_at": current_timestamp,
            "updated_at": current_timestamp,
            "success": True
        }
