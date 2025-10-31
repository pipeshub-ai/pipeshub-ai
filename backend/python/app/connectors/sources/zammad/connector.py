"""Zammad Connector Implementation"""
import re
import base64
from datetime import datetime, timedelta
from logging import Logger
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import uuid4

from fastapi.responses import StreamingResponse  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
)
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import (
    DataStoreProvider,
    TransactionStore,
)
from app.connectors.core.base.sync_point.sync_point import SyncDataPointType, SyncPoint
from app.connectors.core.registry.connector_builder import (
    AuthField,
    ConnectorBuilder,
    DocumentationLink,
)
from app.connectors.sources.zammad.apps import ZammadApp
from app.connectors.sources.zammad.models import (
    ZammadGroup,
    ZammadKBAnswer,
    ZammadKBCategory,
    ZammadKnowledgeBase,
    ZammadOrganization,
    ZammadRole,
    ZammadTicket,
    ZammadUser,
)
from app.models.entities import (
    AppUser,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.zammad.zammad import (
    ZammadClient,
    ZammadTokenConfig,
)
from app.sources.external.zammad.zammad import ZammadDataSource, ZammadResponse
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.sources.client.http.http_request import HTTPRequest

THRESHOLD_PAGINATION_LIMIT: int = 100

# Zammad default role IDs
ZAMMAD_ADMIN_ROLE_ID: int = 1
ZAMMAD_AGENT_ROLE_ID: int = 2
ZAMMAD_CUSTOMER_ROLE_ID: int = 3

@ConnectorBuilder("Zammad")\
    .in_group("Support & Helpdesk")\
    .with_auth_type("API_KEY")\
    .with_description("Sync tickets and users from Zammad")\
    .with_categories(["Support", "Ticketing"])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/zammad.svg")
        .with_realtime_support(True)
        .add_documentation_link(DocumentationLink(
            "Zammad API Setup",
            "https://docs.zammad.org/en/latest/api/intro.html"
        ))
        .add_auth_field(AuthField(
            name="baseUrl",
            display_name="Zammad Base URL",
            placeholder="https://your-domain.zammad.com",
            description="Your Zammad instance URL"
        ))
        .add_auth_field(AuthField(
            name="token",
            display_name="API Token",
            placeholder="Enter your Zammad API token",
            description="API token from Zammad user profile",
            field_type="PASSWORD",
            is_secret=True
        ))
        .with_webhook_config(True, ["ticket.created", "ticket.updated", "user.created", "user.updated"])
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
    )\
    .build_decorator()
class ZammadConnector(BaseConnector):
    """Zammad connector for syncing tickets, users, groups, and organizations"""

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService
    ) -> None:
        super().__init__(ZammadApp(), logger, data_entities_processor, data_store_provider, config_service)
        self.zammad_client: Optional[ZammadClient] = None
        self.zammad_datasource: Optional[ZammadDataSource] = None
        self.base_url: Optional[str] = None
        # entities data
        self.sub_orgs_data: List[ZammadOrganization] = []
        self.users_data: List[ZammadUser] = []
        self.groups_data: List[ZammadGroup] = []
        self.tickets_data: List[ZammadTicket] = []
        self.knowledge_base_data: List[ZammadKnowledgeBase] = []
        self.kb_categories_data: List[ZammadKBCategory] = []
        self.kb_answers_data: List[ZammadKBAnswer] = []
        self.roles_data: List[ZammadRole] = []

        # Initialize sync points for delta sync
        self._init_sync_points()

        # Buffer time for catching race conditions (5 minutes)
        self.buffer_minutes: int = 5

    def _init_sync_points(self) -> None:
        """Initialize sync points for tracking last sync timestamps"""
        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_name=Connectors.ZAMMAD,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider
            )

        self.tickets_sync_point = _create_sync_point(SyncDataPointType.RECORDS)
        self.users_sync_point = _create_sync_point(SyncDataPointType.USERS)
        self.webhook_sync_point = _create_sync_point(SyncDataPointType.RECORDS)

    async def init(self) -> None:
        """Initialize Zammad client using token from config"""
        try:
            # Get Zammad configuration
            config: Optional[Dict[str, Any]] = await self.config_service.get_config("/services/connectors/zammad/config")

            if not config:
                raise ValueError("Zammad configuration not found")

            auth_config: Dict[str, Any] = config.get("auth", {})
            self.base_url = auth_config.get("base_url") or auth_config.get("baseUrl")
            token: Optional[str] = auth_config.get("token")

            if not self.base_url or not token:
                raise ValueError("Zammad base_url and token are required")

            # Create Zammad client
            zammad_config: ZammadTokenConfig = ZammadTokenConfig(
                base_url=self.base_url,
                token=token
            )

            self.zammad_client = ZammadClient.build_with_config(zammad_config)
            self.zammad_datasource = ZammadDataSource(self.zammad_client)

            self.logger.info("Zammad client initialized successfully")

        except Exception as e:
            self.logger.error(f"Failed to initialize Zammad client: {e}")
            raise

    async def run_sync(self) -> None:
        """Main sync flow - sync users, organizations, groups, and tickets"""
        try:
            self.logger.info("Starting Zammad sync")

            # Ensure client is initialized
            if not self.zammad_datasource:
                await self.init()
            await self.__fetch_entities()
            await self.__build_nodes_and_edges()
            self.logger.info("Zammad sync completed successfully")

        except Exception as e:
            self.logger.error(f"Error during Zammad sync: {e}", exc_info=True)
            raise

    async def __build_nodes_and_edges(self) -> None:
        """Build nodes and edges for the Zammad connector"""
        try:
            self.logger.info("Building nodes and edges for the Zammad connector")

            # Step 1: Get org_id and app_key
            org_id: Optional[str] = self.data_entities_processor.org_id
            if not org_id:
                raise ValueError("Organization ID not found")

            # Get app_key before transaction (read operation)
            apps: List[Dict[str, Any]] = await self.data_store_provider.arango_service.get_org_apps(org_id)
            app_key: Optional[str] = next((a.get("_key") for a in apps if a.get("type") == Connectors.ZAMMAD.value), None)

            if not app_key:
                self.logger.warning("Zammad app not found for organization")
                return

            self.logger.info(f"Found Zammad app with key: {app_key}")

            async with self.data_store_provider.transaction() as tx_store:

                # Step 2: Create static role nodes
                await self.__create_role_nodes(tx_store)

                # Step 3: Process sub-organizations as RecordGroups
                sub_org_map: Dict[str, str] = await self.__process_sub_organizations(tx_store, org_id, app_key)

                # Step 4: Process users with all relationships
                user_id_map: Dict[str, str] = await self.__process_users(tx_store, org_id, app_key, sub_org_map)

                # Step 5: Process tickets as Records with TicketRecords
                await self.__process_tickets(tx_store, org_id, sub_org_map, user_id_map)

            # Step 6: Process KB categories as RecordGroups and KB answers as WebpageRecords
            # Done in a separate transaction after tickets to avoid complexity
            self.logger.info("STEP 6: Processing Knowledge Base")
            if self.kb_categories_data or self.kb_answers_data:
                self.logger.info(f"Processing {len(self.kb_categories_data)} KB categories and {len(self.kb_answers_data)} KB answers...")
                await self.__process_knowledge_base(org_id, app_key)
                self.logger.info("KB processing completed!")
            else:
                self.logger.info("Skipping KB processing - no KB data found")

            self.logger.info("Successfully built all nodes and edges for Zammad connector")

        except Exception as e:
            self.logger.error(f"Error building nodes and edges: {e}", exc_info=True)
            raise

    async def __create_role_nodes(self, tx_store: TransactionStore) -> None:
        """Create static role nodes for Admin, Agent, Customer"""
        try:
            self.logger.info("Creating static role nodes")

            role_nodes: List[Dict[str, Any]] = []
            for role_name in ["Admin", "Agent", "Customer"]:
                role_key: str = f"zammad_{role_name.lower()}"
                role_node: Dict[str, Any] = {
                    "_key": role_key,
                    "roleName": role_name,
                    "connectorName": Connectors.ZAMMAD.value,
                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms()
                }
                role_nodes.append(role_node)

            # Create roles collection nodes - using direct collection access
            # Note: Roles should be in a custom collection or types collection
            # For now, we'll store them in the groups collection as role types
            await tx_store.arango_service.batch_upsert_nodes(
                role_nodes,
                CollectionNames.GROUPS.value,
                transaction=tx_store.txn
            )

            self.logger.info(f"Created {len(role_nodes)} role nodes")

        except Exception as e:
            self.logger.error(f"Error creating role nodes: {e}", exc_info=True)
            raise

    async def __process_sub_organizations(self, tx_store: TransactionStore, org_id: str, app_key: str) -> Dict[str, str]:
        """Process sub-organizations as RecordGroups and return mapping of external_id to record_group_id"""
        try:
            self.logger.info(f"Processing {len(self.sub_orgs_data)} sub-organizations")

            sub_org_map: Dict[str, str] = {}  # Maps Zammad org ID to RecordGroup ID
            record_groups_with_permissions: List[Tuple[RecordGroup, List[Permission]]] = []

            for sub_org in self.sub_orgs_data:
                external_org_id: str = str(sub_org.id)

                # Convert datetime to epoch timestamp in milliseconds
                created_at: int = int(sub_org.created_at.timestamp() * 1000) if sub_org.created_at else get_epoch_timestamp_in_ms()
                updated_at: int = int(sub_org.updated_at.timestamp() * 1000) if sub_org.updated_at else get_epoch_timestamp_in_ms()

                # Create RecordGroup object - let on_new_record_groups handle ID assignment
                record_group = RecordGroup(
                    org_id=org_id,
                    name=sub_org.name,
                    external_group_id=external_org_id,
                    connector_name=Connectors.ZAMMAD,
                    group_type=RecordGroupType.ZAMMAD_SUB_ORGANIZATION_GROUP,
                    created_at=created_at,
                    updated_at=updated_at,
                    source_created_at=created_at,
                    source_updated_at=updated_at
                )

                # No permissions for sub-orgs (permissions are per-ticket)
                record_groups_with_permissions.append((record_group, []))

            # Use the data processor to handle deduplication and edge creation
            if record_groups_with_permissions:
                await self.data_entities_processor.on_new_record_groups(record_groups_with_permissions)
                self.logger.info(f"Processed {len(record_groups_with_permissions)} sub-organization RecordGroups")

                # Build the mapping after on_new_record_groups has assigned IDs
                for record_group, _ in record_groups_with_permissions:
                    sub_org_map[record_group.external_group_id] = record_group.id

                # Create belongsTo edges from RecordGroups to ZAMMAD app
                record_group_app_edges: List[Dict[str, Any]] = []
                for record_group, _ in record_groups_with_permissions:
                    record_group_app_edge: Dict[str, Any] = {
                        "_from": f"{CollectionNames.RECORD_GROUPS.value}/{record_group.id}",
                        "_to": f"{CollectionNames.APPS.value}/{app_key}",
                        "entityType": "KB",
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms()
                    }
                    record_group_app_edges.append(record_group_app_edge)

                if record_group_app_edges:
                    await tx_store.batch_create_edges(
                        record_group_app_edges,
                        CollectionNames.BELONGS_TO.value
                    )
                    self.logger.info(f"Created {len(record_group_app_edges)} RecordGroup-to-App belongsTo edges")

            return sub_org_map

        except Exception as e:
            self.logger.error(f"Error processing sub-organizations: {e}", exc_info=True)
            raise

    async def __process_users(self, tx_store: TransactionStore, org_id: str, app_key: str, sub_org_map: Dict[str, str]) -> Dict[str, str]:
        """Process users and create all necessary edges. Returns mapping of external_user_id -> internal_user_id"""
        try:
            self.logger.info(f"Processing {len(self.users_data)} users")

            app_users: List[AppUser] = []
            user_metadata: List[Dict[str, Any]] = []  # Store metadata for edge creation
            user_id_map: Dict[str, str] = {}  # Maps Zammad user ID to internal user ID

            # Step 1: Create AppUser objects and collect metadata
            for user in self.users_data:
                external_user_id: str = str(user.id)
                email: str = user.email
                if not email:
                    self.logger.warning(f"Skipping user without email: {user.id}")
                    continue

                full_name: str = f"{user.firstname} {user.lastname}".strip()

                # Convert datetime to epoch timestamp in milliseconds
                created_at: int = int(user.created_at.timestamp() * 1000) if user.created_at else get_epoch_timestamp_in_ms()
                updated_at: int = int(user.updated_at.timestamp() * 1000) if user.updated_at else get_epoch_timestamp_in_ms()

                # Create AppUser - let on_new_app_users handle ID assignment
                app_user: AppUser = AppUser(
                    app_name=Connectors.ZAMMAD,
                    source_user_id=external_user_id,
                    org_id=org_id,
                    email=email,
                    full_name=full_name if full_name else email,
                    is_active=user.active,
                    created_at=created_at,
                    updated_at=updated_at,
                    source_created_at=created_at,
                    source_updated_at=updated_at
                )
                app_users.append(app_user)

                # Store metadata for edge creation later
                user_metadata.append({
                    'external_user_id': external_user_id,
                    'email': email,
                    'role_key': self.__get_user_role_key(user),
                    'organization_id': user.organization_id
                })

            # Step 2: Process users through data_entities_processor (handles deduplication)
            if app_users:
                await self.data_entities_processor.on_new_app_users(app_users)
                self.logger.info(f"Processed {len(app_users)} users through on_new_app_users")

            # Step 3: Fetch actual user IDs from database and build mapping
            user_app_edges: List[Dict[str, Any]] = []
            user_role_edges: List[Dict[str, Any]] = []
            user_suborg_edges: List[Dict[str, Any]] = []

            for metadata in user_metadata:
                # Fetch user from DB to get actual ID (handles existing users)
                db_user = await tx_store.get_user_by_email(metadata['email'])
                if not db_user:
                    self.logger.warning(f"User {metadata['email']} not found in DB after on_new_app_users")
                    continue

                user_id: str = db_user.id
                user_id_map[metadata['external_user_id']] = user_id

                # Create UserAppRelation edge
                user_app_edge: Dict[str, Any] = {
                    "_from": f"{CollectionNames.USERS.value}/{user_id}",
                    "_to": f"{CollectionNames.APPS.value}/{app_key}",
                    "syncState": "NOT_STARTED",
                    "lastSyncUpdate": get_epoch_timestamp_in_ms()
                }
                user_app_edges.append(user_app_edge)

                # Create isOfType edge for user role
                user_role_edge: Dict[str, Any] = {
                    "_from": f"{CollectionNames.USERS.value}/{user_id}",
                    "_to": f"{CollectionNames.GROUPS.value}/{metadata['role_key']}",
                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms()
                }
                user_role_edges.append(user_role_edge)

                # Create belongsTo edge to sub-organization
                user_org_id: Optional[int] = metadata['organization_id']
                if user_org_id and str(user_org_id) in sub_org_map:
                    record_group_id: str = sub_org_map[str(user_org_id)]
                    user_suborg_edge: Dict[str, Any] = {
                        "_from": f"{CollectionNames.USERS.value}/{user_id}",
                        "_to": f"{CollectionNames.RECORD_GROUPS.value}/{record_group_id}",
                        "entityType": "KB",
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms()
                    }
                    user_suborg_edges.append(user_suborg_edge)

            # Step 4: Create all edges with correct user IDs
            if user_app_edges:
                await tx_store.batch_create_edges(user_app_edges, CollectionNames.USER_APP_RELATION.value)
                self.logger.info(f"Created {len(user_app_edges)} UserAppRelation edges")

            if user_role_edges:
                await tx_store.batch_create_edges(user_role_edges, CollectionNames.IS_OF_TYPE.value)
                self.logger.info(f"Created {len(user_role_edges)} user-role isOfType edges")

            if user_suborg_edges:
                await tx_store.batch_create_edges(user_suborg_edges, CollectionNames.BELONGS_TO.value)
                self.logger.info(f"Created {len(user_suborg_edges)} user-subOrg belongsTo edges")

            return user_id_map

        except Exception as e:
            self.logger.error(f"Error processing users: {e}", exc_info=True)
            raise

    def __get_user_role_key(self, user: ZammadUser) -> str:
        """Determine user role from Zammad role_ids and return the role key"""
        role_ids: List[int] = user.role_ids

        # Map Zammad roles to our role types
        # Typically: Admin (role_id=1), Agent (role_id=2), Customer (role_id=3)
        # This is a simplified mapping - adjust based on your Zammad setup
        if not role_ids:
            return "zammad_customer"  # Default to customer

        # Check roles_data for role names if available
        if self.roles_data:
            for role_id in role_ids:
                role: Optional[ZammadRole] = next((r for r in self.roles_data if r.id == role_id), None)
                if role:
                    role_name: str = role.name.lower()
                    if "admin" in role_name:
                        return "zammad_admin"
                    elif "agent" in role_name:
                        return "zammad_agent"

        # Fallback: assume first role determines the type
        if ZAMMAD_ADMIN_ROLE_ID in role_ids:
            return "zammad_admin"
        elif ZAMMAD_AGENT_ROLE_ID in role_ids:
            return "zammad_agent"
        else:
            return "zammad_customer"

    async def __process_tickets(self, tx_store: TransactionStore, org_id: str, sub_org_map: Dict[str, str], user_id_map: Dict[str, str]) -> None:
        """Process tickets as Records with TicketRecords and create edges"""
        try:
            self.logger.info(f"Processing {len(self.tickets_data)} tickets")

            record_ticket_edges: List[Dict[str, Any]] = []  # isOfType edges
            record_suborg_edges: List[Dict[str, Any]] = []  # belongsTo edges
            user_permission_edges: List[Dict[str, Any]] = []  # permission edges from users to records
            records_with_permissions: List[Tuple[TicketRecord, List[Permission]]] = []  # For on_new_records dispatch
            # self.logger.info(f"Tickets data: {self.tickets_data}")

            for ticket in self.tickets_data:
                external_ticket_id: str = str(ticket.id)

                # Check if record already exists (for deduplication)
                existing_record = await tx_store.get_record_by_external_id(
                    connector_name=Connectors.ZAMMAD,
                    external_id=external_ticket_id,
                    record_type="TICKET"
                )
                record_id: str = existing_record.id if existing_record else str(uuid4())
                is_new: bool = existing_record is None

                # Convert datetime to epoch timestamp in milliseconds
                created_at: int = int(ticket.created_at.timestamp() * 1000) if ticket.created_at else get_epoch_timestamp_in_ms()
                updated_at: int = int(ticket.updated_at.timestamp() * 1000) if ticket.updated_at else get_epoch_timestamp_in_ms()

                # Get organization ID
                ticket_org_id: Optional[int] = ticket.organization_id
                external_group_id: Optional[str] = str(ticket_org_id) if ticket_org_id else None

                # Create TicketRecord (don't set external_record_group_id - we handle belongsTo edges manually)
                ticket_record: TicketRecord = TicketRecord(
                    id=record_id,
                    version=0 if is_new else existing_record.version + 1,
                    org_id=org_id,
                    record_name=ticket.title or f"Ticket #{external_ticket_id}",
                    record_type=RecordType.TICKET,
                    external_record_id=external_ticket_id,
                    connector_name=Connectors.ZAMMAD,
                    origin=OriginTypes.CONNECTOR,
                    mime_type=MimeTypes.PLAIN_TEXT.value,
                    weburl=f"{self.base_url}/#ticket/zoom/{external_ticket_id}" if self.base_url else None,
                    created_at=created_at,
                    updated_at=updated_at,
                    source_created_at=created_at,
                    source_updated_at=updated_at,
                    summary=ticket.title or "",
                    description=ticket.note or "",
                    status=ticket.state or "",
                    priority=ticket.priority or "",
                    assignee=str(ticket.owner_id) if ticket.owner_id else None,
                    reporter_email=None,
                    assignee_email=None
                )

                # Build permissions list for this record
                record_permissions: List[Permission] = []

                # Add permission for customer (reporter)
                customer_id: str = str(ticket.customer_id) if ticket.customer_id else ""
                if customer_id and customer_id in user_id_map:
                    customer_user_id: str = user_id_map[customer_id]
                    customer_permission: Permission = Permission(
                        external_id=customer_id,
                        email=None,
                        type=PermissionType.READ,
                        entity_type=EntityType.USER
                    )
                    record_permissions.append(customer_permission)

                    # Create permission edge from user to record
                    user_permission_edge: Dict[str, Any] = {
                        "_from": f"{CollectionNames.USERS.value}/{customer_user_id}",
                        "_to": f"{CollectionNames.RECORDS.value}/{record_id}",
                        "role": PermissionType.READ.value,
                        "type": EntityType.USER.value,
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms()
                    }
                    user_permission_edges.append(user_permission_edge)

                # Add permission for assignee (agent/owner)
                owner_id: str = str(ticket.owner_id) if ticket.owner_id else ""
                if owner_id and owner_id in user_id_map and owner_id != customer_id:
                    owner_user_id: str = user_id_map[owner_id]
                    owner_permission: Permission = Permission(
                        external_id=owner_id,
                        email=None,
                        type=PermissionType.WRITE,
                        entity_type=EntityType.USER
                    )
                    record_permissions.append(owner_permission)

                    # Create permission edge from user to record
                    user_permission_edge: Dict[str, Any] = {
                        "_from": f"{CollectionNames.USERS.value}/{owner_user_id}",
                        "_to": f"{CollectionNames.RECORDS.value}/{record_id}",
                        "role": PermissionType.WRITE.value,
                        "type": EntityType.USER.value,
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms()
                    }
                    user_permission_edges.append(user_permission_edge)

                # Add permissions for all users in the same sub-organization
                if external_group_id and external_group_id in sub_org_map:
                    for user in self.users_data:
                        user_external_id: str = str(user.id)
                        user_org_id: str = str(user.organization_id) if user.organization_id else ""

                        # If user belongs to same organization and not already added
                        if user_org_id == external_group_id and user_external_id in user_id_map:
                            if user_external_id not in [customer_id, owner_id]:
                                internal_user_id: str = user_id_map[user_external_id]
                                org_user_permission: Permission = Permission(
                                    external_id=user_external_id,
                                    email=None,
                                    type=PermissionType.READ,
                                    entity_type=EntityType.USER
                                )
                                record_permissions.append(org_user_permission)

                                # Create permission edge from user to record
                                user_permission_edge: Dict[str, Any] = {
                                    "_from": f"{CollectionNames.USERS.value}/{internal_user_id}",
                                    "_to": f"{CollectionNames.RECORDS.value}/{record_id}",
                                    "role": PermissionType.READ.value,
                                    "type": EntityType.USER.value,
                                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                                    "updatedAtTimestamp": get_epoch_timestamp_in_ms()
                                }
                                user_permission_edges.append(user_permission_edge)

                # Add to records_with_permissions for on_new_records
                records_with_permissions.append((ticket_record, record_permissions))

                # Create isOfType edge (record -> ticketRecord)
                record_ticket_edge: Dict[str, Any] = {
                    "_from": f"{CollectionNames.RECORDS.value}/{record_id}",
                    "_to": f"{CollectionNames.TICKETS.value}/{record_id}",
                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms()
                }
                record_ticket_edges.append(record_ticket_edge)

                # Create belongsTo edge (record -> sub-organization RecordGroup)
                if external_group_id and external_group_id in sub_org_map:
                    record_group_id: str = sub_org_map[external_group_id]
                    record_suborg_edge: Dict[str, Any] = {
                        "_from": f"{CollectionNames.RECORDS.value}/{record_id}",
                        "_to": f"{CollectionNames.RECORD_GROUPS.value}/{record_group_id}",
                        "entityType": "KB",
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms()
                    }
                    record_suborg_edges.append(record_suborg_edge)

            # Process records through data_entities_processor for storage + Kafka publishing
            # Only send NEW records to avoid re-indexing existing ones
            new_records_with_permissions = [
                (record, perms) for record, perms in records_with_permissions 
                if record.version == 0  # version 0 means it's a new record
            ]
            
            if new_records_with_permissions:
                self.logger.info("Indexing the following ticket records:")
                for record, perms in new_records_with_permissions:
                    self.logger.info(f"Record: id={record.id}, title={getattr(record, 'record_name', None)}, external_id={record.external_record_id}")
                await self.data_entities_processor.on_new_records(new_records_with_permissions)
                self.logger.info(f"Dispatched {len(new_records_with_permissions)} NEW records for indexing (skipped {len(records_with_permissions) - len(new_records_with_permissions)} existing)")
            else:
                self.logger.info(f"No new records to dispatch - all {len(records_with_permissions)} records already exist")

            # Create isOfType, belongsTo, and hasAccess edges in a separate transaction
            async with self.data_store_provider.transaction() as tx_store_2:
                # Create isOfType edges (record -> ticketRecord)
                if record_ticket_edges:
                    await tx_store_2.batch_create_edges(record_ticket_edges, CollectionNames.IS_OF_TYPE.value)
                    self.logger.info(f"Created {len(record_ticket_edges)} record-ticket isOfType edges")

                # Create belongsTo edges (record -> sub-organization)
                if record_suborg_edges:
                    await tx_store_2.batch_create_edges(record_suborg_edges, CollectionNames.BELONGS_TO.value)
                    self.logger.info(f"Created {len(record_suborg_edges)} record-subOrg belongsTo edges")

                # Create permission edges (user -> record)
                if user_permission_edges:
                    await tx_store_2.batch_create_edges(user_permission_edges, CollectionNames.PERMISSION.value)
                    self.logger.info(f"Created {len(user_permission_edges)} user-record permission edges")

        except Exception as e:
            self.logger.error(f"Error processing tickets: {e}", exc_info=True)
            raise

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Fetch and stream record content (tickets or KB answers)"""
        try:
            if not self.zammad_datasource:
                await self.init()

            # Check record type to determine how to stream
            if record.record_type == RecordType.TICKET:
                return await self.__stream_ticket(record)
            elif record.record_type == RecordType.WEBPAGE:
                return await self.__stream_kb_answer(record)
            else:
                return StreamingResponse(
                    iter(["Unsupported record type"]),
                    media_type=MimeTypes.PLAIN_TEXT.value
                )

        except Exception as e:
            self.logger.error(f"Error streaming record: {e}")
            return StreamingResponse(
                iter([f"Error: {str(e)}"]),
                media_type=MimeTypes.PLAIN_TEXT.value
            )

    async def __stream_ticket(self, record: Record) -> StreamingResponse:
        """Stream ticket content with articles"""
        try:
            ticket_id: int = int(record.external_record_id)
            
            # Fetch ticket details
            ticket_response: ZammadResponse = await self.zammad_datasource.get_ticket(ticket_id)
            if not ticket_response.success or not ticket_response.data:
                return StreamingResponse(
                    iter(["Ticket not found"]),
                    media_type=MimeTypes.PLAIN_TEXT.value
                )

            ticket_dict: Dict[str, Any] = ticket_response.data if isinstance(ticket_response.data, dict) else {}
            ticket: ZammadTicket = ZammadTicket(**ticket_dict)

            # Fetch ticket articles
            articles_response: ZammadResponse = await self.zammad_datasource.list_ticket_articles(ticket_id)
            articles: List[Dict[str, Any]] = []
            if articles_response.success and articles_response.data:
                articles = articles_response.data if isinstance(articles_response.data, list) else []

            # Generate content
            content: str = await self.__get_ticket_content(ticket, articles)

            return StreamingResponse(
                iter([content]),
                media_type=MimeTypes.PLAIN_TEXT.value,
                headers={}
            )

        except Exception as e:
            self.logger.error(f"Error streaming ticket: {e}")
            return StreamingResponse(
                iter([f"Error: {str(e)}"]),
                media_type=MimeTypes.PLAIN_TEXT.value
            )

    async def __stream_kb_answer(self, record: Record) -> StreamingResponse:
        """Stream KB answer content"""
        try:
            answer_id: int = int(record.external_record_id)
            # Extract KB ID from metadata or use default (1)
            kb_id: int = 1  # Default to KB 1
            self.logger.info(f"Fetched KB ID: {kb_id} for answer ID: {answer_id}")
            self.logger.info(f"Streaming record data: {record}")

            # Fetch KB answer details with full content
            answer_response: ZammadResponse = await self.zammad_datasource.get_kb_answer(
                kb_id=kb_id,
                id=answer_id,
                full=True,
                include_contents=True
            )
            self.logger.info(f"Fetched KB Answer Response: {answer_response}")
            if not answer_response.success or not answer_response.data:
                return StreamingResponse(
                    iter(["KB Answer not found"]),
                    media_type=MimeTypes.HTML.value
                )

            # Extract answer from assets
            answer_dict = answer_response.data.get('assets', {}).get('KnowledgeBaseAnswer', {}).get(str(answer_id), {})
            self.logger.info(f"Fetched KB Answer dict: {answer_dict}")
            if not answer_dict:
                return StreamingResponse(
                    iter(["KB Answer data not found in response"]),
                    media_type=MimeTypes.HTML.value
                )
            
            answer: ZammadKBAnswer = ZammadKBAnswer(**answer_dict)
            self.logger.info(f"Fetched KB Answer: {answer}")
            # Generate content from translations
            content: str = await self.__get_kb_answer_content(answer, answer_response.data)

            return StreamingResponse(
                iter([content]),
                media_type=MimeTypes.HTML.value,
                headers={}
            )

        except Exception as e:
            self.logger.error(f"Error streaming KB answer: {e}")
            return StreamingResponse(
                iter([f"Error: {str(e)}"]),
                media_type=MimeTypes.HTML.value
            )

    async def test_connection_and_access(self) -> bool:
        """Test Zammad API connection"""
        try:
            if not self.zammad_datasource:
                await self.init()

            response: ZammadResponse = await self.zammad_datasource.get_current_user()

            if response.success:
                self.logger.info("Zammad connection test successful")
                return True
            else:
                self.logger.error(f"Zammad connection test failed: {response.error}")
                return False

        except Exception as e:
            self.logger.error(f"Zammad connection test failed: {e}")
            return False

    async def get_signed_url(self, record: Record) -> str:
        """Return the weburl (Zammad doesn't need signed URLs)"""
        return record.weburl or ""

    async def run_incremental_sync(self) -> None:
        """Implement incremental sync using updated_at timestamps and sync points"""
        pass

    async def cleanup(self) -> None:
        """Cleanup resources"""
        try:
            if self.zammad_client:
                # Close any open connections if needed
                pass
            self.logger.info("Zammad connector cleanup completed")
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")

    async def handle_webhook_notification(self, notification: Dict[str, Any]) -> None:
        """Handle webhook events for real-time updates"""
        pass

    async def __fetch_entities(self) -> None:
        """Fetch entities from Zammad"""
        try:
            # Clear KB data from any previous runs
            self.kb_categories_data = []
            self.kb_answers_data = []
            
            # 1. Fetch sub-organizations from Zammad
            self.logger.info("Fetching sub-organizations from Zammad")
            self.sub_orgs_data = await self.__fetch_sub_organizations()
            if not self.sub_orgs_data or len(self.sub_orgs_data) == 0:
                self.logger.debug("No sub-organizations found or failed to fetch sub-organizations")
                return
            self.logger.info(f"Fetched {len(self.sub_orgs_data)} sub-organizations")

            # 2. Fetch users from Zammad
            self.logger.info("Fetching users from Zammad")
            self.users_data = await self.__fetch_users()
            if not self.users_data or len(self.users_data) == 0:
                self.logger.debug("No users found or failed to fetch users")
            self.logger.info(f"Fetched {len(self.users_data)} users from Zammad")

            # 3. Fetch groups from Zammad
            self.logger.info("Fetching groups from Zammad")
            self.groups_data = await self.__fetch_groups()
            if not self.groups_data or len(self.groups_data) == 0:
                self.logger.debug("No groups found or failed to fetch groups")
            self.logger.info(f"Fetched {len(self.groups_data)} groups from Zammad")

            # 4. Fetch tickets from Zammad
            self.logger.info("Fetching tickets from Zammad")
            self.tickets_data = await self.__fetch_tickets()
            if not self.tickets_data or len(self.tickets_data) == 0:
                self.logger.debug("No tickets found or failed to fetch tickets")
            self.logger.info(f"Fetched {len(self.tickets_data)} tickets from Zammad")

            # 5. Fetch Knowledge Base from Zammad
            self.logger.info("STEP 5: Fetching Knowledge Base from Zammad")
            self.knowledge_base_data = await self.__fetch_knowledge_base()
            # All categories and answers are fetched in init_knowledge_base, no need to fetch again
            if not self.knowledge_base_data or len(self.knowledge_base_data) == 0:
                self.logger.warning("No Knowledge Base found or failed to fetch Knowledge Base")
                self.logger.info("This is normal if your Zammad instance has no KB configured")
            else:
                self.logger.info(f"Fetched {len(self.knowledge_base_data)} Knowledge Base from Zammad")
                self.logger.info(f"KB SUMMARY: {len(self.kb_categories_data)} categories, {len(self.kb_answers_data)} answers")

            # 6. Fetch Roles from Zammad
            self.logger.info("Fetching Roles from Zammad")
            self.roles_data = await self.__fetch_roles()
            if not self.roles_data or len(self.roles_data) == 0:
                self.logger.debug("No Roles found or failed to fetch Roles")
            self.logger.info(f"Fetched {len(self.roles_data)} Roles from Zammad")

        except Exception as e:
            self.logger.error(f"Error fetching entities: {e}", exc_info=True)
            raise

    async def __fetch_sub_organizations(self) -> List[ZammadOrganization]:
        """Fetch Zammad sub-organizations"""
        try:
            response: ZammadResponse = await self.zammad_datasource.list_organizations(expand="true")
            if not response.success or not response.data:
                return []

            organizations: List[ZammadOrganization] = []
            data_list: List[Dict[str, Any]] = response.data if isinstance(response.data, list) else []
            for org_data in data_list:
                try:
                    org: ZammadOrganization = ZammadOrganization(**org_data)
                    organizations.append(org)
                except Exception as e:
                    self.logger.warning(f"Failed to parse organization {org_data.get('id')}: {e}")

            return organizations
        except Exception as e:
            self.logger.error(f"Error fetching sub-organizations: {e}", exc_info=True)
            return []

    async def __fetch_users(self) -> List[ZammadUser]:
        """Fetch Zammad users"""
        try:
            response: ZammadResponse = await self.zammad_datasource.list_users(expand="true")
            if not response.success or not response.data:
                return []

            users: List[ZammadUser] = []
            data_list: List[Dict[str, Any]] = response.data if isinstance(response.data, list) else []
            for user_data in data_list:
                try:
                    user: ZammadUser = ZammadUser(**user_data)
                    users.append(user)
                except Exception as e:
                    self.logger.warning(f"Failed to parse user {user_data.get('id')}: {e}")

            return users
        except Exception as e:
            self.logger.error(f"Error fetching users: {e}", exc_info=True)
            return []

    async def __fetch_groups(self) -> List[ZammadGroup]:
        """Fetch Zammad groups"""
        try:
            response: ZammadResponse = await self.zammad_datasource.list_groups(expand="true")
            if not response.success or not response.data:
                return []

            groups: List[ZammadGroup] = []
            data_list: List[Dict[str, Any]] = response.data if isinstance(response.data, list) else []
            for group_data in data_list:
                try:
                    group: ZammadGroup = ZammadGroup(**group_data)
                    groups.append(group)
                except Exception as e:
                    self.logger.warning(f"Failed to parse group {group_data.get('id')}: {e}")

            return groups
        except Exception as e:
            self.logger.error(f"Error fetching groups: {e}", exc_info=True)
            return []

    async def __fetch_tickets(self) -> List[ZammadTicket]:
        """Fetch Zammad tickets"""
        try:
            response: ZammadResponse = await self.zammad_datasource.list_tickets(expand="true")
            self.logger.info("fetched tickets: %s", len(response.data) if response.data else 0)
            if not response.success or not response.data:
                return []

            tickets: List[ZammadTicket] = []
            data_list: List[Dict[str, Any]] = response.data if isinstance(response.data, list) else []
            for ticket_data in data_list:
                try:
                    ticket: ZammadTicket = ZammadTicket(**ticket_data)
                    tickets.append(ticket)
                except Exception as e:
                    self.logger.warning(f"Failed to parse ticket {ticket_data.get('id')}: {e}")

            return tickets
        except Exception as e:
            self.logger.error(f"Error fetching tickets: {e}", exc_info=True)
            return []

    async def __fetch_knowledge_base(self) -> None:
        """Fetch and parse Zammad Knowledge Base using init_knowledge_base response"""
        try:
            response: ZammadResponse = await self.zammad_datasource.init_knowledge_base()
            if not response.success or not response.data:
                self.knowledge_base_data = []
                self.kb_categories_data = []
                self.kb_answers_data = []
                return

            data = response.data
            kb_map = data.get("KnowledgeBase", {})
            kb_translations = data.get("KnowledgeBaseTranslation", {})
            category_map = data.get("KnowledgeBaseCategory", {})
            category_translations = data.get("KnowledgeBaseCategoryTranslation", {})
            answer_map = data.get("KnowledgeBaseAnswer", {})
            answer_translations = data.get("KnowledgeBaseAnswerTranslation", {})

            # 1. Parse KnowledgeBase objects
            self.knowledge_base_data = []
            for kb_id, kb in kb_map.items():
                title = ""
                translation_ids = kb.get("translation_ids", [])
                if translation_ids:
                    translation_id = str(translation_ids[0])
                    translation = kb_translations.get(translation_id, {})
                    title = translation.get("title", "")
                kb_obj = ZammadKnowledgeBase(
                    id=kb.get("id"),
                    title=title,
                    created_at=datetime.fromisoformat(kb.get("created_at").replace("Z", "")) if kb.get("created_at") else None,
                    updated_at=datetime.fromisoformat(kb.get("updated_at").replace("Z", "")) if kb.get("updated_at") else None
                )
                self.knowledge_base_data.append(kb_obj)

            # 2. Parse KnowledgeBaseCategory objects with translation titles and extra fields
            self.kb_categories_data = []
            for cat_id, cat in category_map.items():
                title = ""
                translation_ids = cat.get("translation_ids", [])
                if translation_ids:
                    translation_id = str(translation_ids[0])
                    translation = category_translations.get(translation_id, {})
                    title = translation.get("title", "")
                cat_obj = ZammadKBCategory(
                    id=cat.get("id"),
                    knowledge_base_id=cat.get("knowledge_base_id"),
                    parent_id=cat.get("parent_id"),
                    title=title,
                    answer_ids=cat.get("answer_ids", []),
                    child_ids=cat.get("child_ids", []),
                    permission_ids=cat.get("permission_ids", []),
                    created_at=datetime.fromisoformat(cat.get("created_at").replace("Z", "")) if cat.get("created_at") else None,
                    updated_at=datetime.fromisoformat(cat.get("updated_at").replace("Z", "")) if cat.get("updated_at") else None
                )
                self.kb_categories_data.append(cat_obj)

            # 3. Parse KnowledgeBaseAnswer objects with translation titles
            self.kb_answers_data = []
            for ans_id, ans in answer_map.items():
                title = ""
                translation_ids = ans.get("translation_ids", [])
                if translation_ids:
                    translation_id = str(translation_ids[0])
                    translation = answer_translations.get(translation_id, {})
                    title = translation.get("title", "")
                ans_obj = ZammadKBAnswer(
                    id=ans.get("id"),
                    category_id=ans.get("category_id"),
                    title=title,
                    created_at=datetime.fromisoformat(ans.get("created_at").replace("Z", "")) if ans.get("created_at") else None,
                    updated_at=datetime.fromisoformat(ans.get("updated_at").replace("Z", "")) if ans.get("updated_at") else None
                )
                self.kb_answers_data.append(ans_obj)

        except Exception as e:
            self.logger.error(f"Error parsing Knowledge Base from Zammad: {e}", exc_info=True)
            self.knowledge_base_data = []
            self.kb_categories_data = []
            self.kb_answers_data = []

    async def __fetch_roles(self) -> List[ZammadRole]:
        """Fetch Zammad Roles"""
        try:
            response: ZammadResponse = await self.zammad_datasource.list_roles(expand="true")
            if not response.success or not response.data:
                return []

            roles: List[ZammadRole] = []
            data_list: List[Dict[str, Any]] = response.data if isinstance(response.data, list) else []
            for role_data in data_list:
                try:
                    role: ZammadRole = ZammadRole(**role_data)
                    roles.append(role)
                except Exception as e:
                    self.logger.warning(f"Failed to parse role {role_data.get('id')}: {e}")

            return roles
        except Exception as e:
            self.logger.error(f"Error fetching Roles: {e}", exc_info=True)
            return []

    async def __process_knowledge_base(self, org_id: str, app_key: str) -> None:
        """Process KB categories as RecordGroups and KB answers as WebpageRecords"""
        try:
            self.logger.info(f"   PROCESSING KNOWLEDGE BASE")
            self.logger.info(f"   Categories to process: {len(self.kb_categories_data)}")
            self.logger.info(f"   Answers to process: {len(self.kb_answers_data)}")
            self.logger.info(f"   Org ID: {org_id}")
            self.logger.info(f"   App Key: {app_key}")

            # Step 1: Process KB categories as RecordGroups
            self.logger.info("STEP 1: Processing KB Categories as RecordGroups...")
            kb_category_map: Dict[str, str] = await self.__process_kb_categories(org_id, app_key)
            self.logger.info(f" KB Category Map created with {len(kb_category_map)} entries: {kb_category_map}")

            # Step 2: Process KB answers as WebpageRecords
            self.logger.info("STEP 2: Processing KB Answers as WebpageRecords...")
            await self.__process_kb_answers_as_records(org_id, kb_category_map)

            self.logger.info(" Successfully processed Knowledge Base entities")

        except Exception as e:
            self.logger.error(f"Error processing Knowledge Base: {e}", exc_info=True)
            raise

    async def __process_kb_categories(self, org_id: str, app_key: str) -> Dict[str, str]:
        """Process KB categories as RecordGroups. Returns mapping of category_id to record_group_id"""
        try:
            self.logger.info(f" Processing {len(self.kb_categories_data)} KB categories as RecordGroups")

            category_map: Dict[str, str] = {}
            record_groups_with_permissions: List[Tuple[RecordGroup, List[Permission]]] = []

            for category in self.kb_categories_data:
                external_category_id: str = str(category.id)
                self.logger.info(f"    Category ID: {external_category_id}, KB ID: {category.knowledge_base_id}")

                # Convert datetime to epoch timestamp in milliseconds
                created_at: int = int(category.created_at.timestamp() * 1000) if category.created_at else get_epoch_timestamp_in_ms()
                updated_at: int = int(category.updated_at.timestamp() * 1000) if category.updated_at else get_epoch_timestamp_in_ms()

                # Get category name from translations if available
                category_name: str = f"KB Category {external_category_id}"
                # Note: Categories have translations, but we'll use a default name for now
                # You can enhance this to fetch the translation title if needed

                # Create RecordGroup object
                record_group = RecordGroup(
                    org_id=org_id,
                    name=category_name,
                    external_group_id=external_category_id,
                    connector_name=Connectors.ZAMMAD,
                    group_type=RecordGroupType.KB,
                    created_at=created_at,
                    updated_at=updated_at,
                    source_created_at=created_at,
                    source_updated_at=updated_at
                )
                
                self.logger.info(f"      Created RecordGroup: {category_name}")

                # No specific permissions for categories (permissions are per-article)
                record_groups_with_permissions.append((record_group, []))

            # Use the data processor to handle deduplication and edge creation
            if record_groups_with_permissions:
                self.logger.info(f" Calling on_new_record_groups with {len(record_groups_with_permissions)} categories...")
                await self.data_entities_processor.on_new_record_groups(record_groups_with_permissions)
                self.logger.info(f" Processed {len(record_groups_with_permissions)} KB category RecordGroups")

                # Build the mapping after on_new_record_groups has assigned IDs
                for record_group, _ in record_groups_with_permissions:
                    category_map[record_group.external_group_id] = record_group.id
                    self.logger.info(f"   Mapped category {record_group.external_group_id} → RecordGroup {record_group.id}")

                # Create belongsTo edges from RecordGroups to ZAMMAD app
                async with self.data_store_provider.transaction() as tx_store:
                    record_group_app_edges: List[Dict[str, Any]] = []
                    for record_group, _ in record_groups_with_permissions:
                        record_group_app_edge: Dict[str, Any] = {
                            "_from": f"{CollectionNames.RECORD_GROUPS.value}/{record_group.id}",
                            "_to": f"{CollectionNames.APPS.value}/{app_key}",
                            "entityType": "KB",
                            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                            "updatedAtTimestamp": get_epoch_timestamp_in_ms()
                        }
                        record_group_app_edges.append(record_group_app_edge)

                    if record_group_app_edges:
                        await tx_store.batch_create_edges(
                            record_group_app_edges,
                            CollectionNames.BELONGS_TO.value
                        )
                        self.logger.info(f"Created {len(record_group_app_edges)} KB category-to-App belongsTo edges")

            return category_map

        except Exception as e:
            self.logger.error(f"Error processing KB categories: {e}", exc_info=True)
            raise

    async def __process_kb_answers_as_records(self, org_id: str, kb_category_map: Dict[str, str]) -> None:
        """Process KB answers as WebpageRecords with proper indexing"""
        try:
            self.logger.info(f"   Processing {len(self.kb_answers_data)} KB answers as WebpageRecords")
            self.logger.info(f"   KB Category Map: {kb_category_map}")

            # Build user_id_map from users_data for quick lookups
            user_id_map: Dict[str, str] = {}
            async with self.data_store_provider.transaction() as tx_store_users:
                for user in self.users_data:
                    user_external_id: str = str(user.id)
                    db_user = await tx_store_users.get_user_by_email(user.email)
                    if db_user:
                        user_id_map[user_external_id] = db_user.id
            
            self.logger.info(f" Built user_id_map with {len(user_id_map)} users")

            records_with_permissions: List[Tuple[WebpageRecord, List[Permission]]] = []
            kb_answer_edges: List[Dict[str, Any]] = []
            kb_answer_category_edges: List[Dict[str, Any]] = []
            user_permission_edges: List[Dict[str, Any]] = []  # For PERMISSIONS_TO_KB collection

            # Use a single transaction for all KB answer processing (like tickets)
            async with self.data_store_provider.transaction() as tx_store:
                for answer in self.kb_answers_data:
                    external_answer_id: str = str(answer.id)
                    self.logger.info(f"\n    Processing KB Answer ID: {external_answer_id}")

                    # Convert datetime to epoch timestamp in milliseconds
                    created_at: int = int(answer.created_at.timestamp() * 1000) if answer.created_at else get_epoch_timestamp_in_ms()
                    updated_at: int = int(answer.updated_at.timestamp() * 1000) if answer.updated_at else get_epoch_timestamp_in_ms()

                    # Use answer.title directly, fallback to translation, otherwise default
                    answer_title: str = answer.title or f"KB Answer {external_answer_id}"
                    if not answer_title and answer.translations and len(answer.translations) > 0:
                        first_translation = answer.translations[0]
                        answer_title = first_translation.get('title', f"KB Answer {external_answer_id}")
                    
                    self.logger.info(f"      Title: {answer_title}")
                    self.logger.info(f"      Category ID: {answer.category_id}")

                    # Get category ID
                    category_id: str = str(answer.category_id)
                    external_group_id: Optional[str] = category_id if category_id in kb_category_map else None
                    
                    if external_group_id:
                        self.logger.info(f"    Linked to RecordGroup: {kb_category_map[external_group_id]}")
                    else:
                        self.logger.warning(f" Category {category_id} not found in kb_category_map!")

                    # Check if record already exists (for deduplication)
                    existing_record = await tx_store.get_record_by_external_id(
                        connector_name=Connectors.ZAMMAD,
                        external_id=external_answer_id,
                        record_type="WEBPAGE"
                    )

                    record_id: str = existing_record.id if existing_record else str(uuid4())
                    is_new: bool = existing_record is None
                    
                    self.logger.info(f"      Record ID: {record_id} (New: {is_new}, Version: {0 if is_new else existing_record.version + 1})")

                    # Create WebpageRecord for records collection (full model)
                    webpage_record: WebpageRecord = WebpageRecord(
                        id=record_id,
                        version=0 if is_new else existing_record.version + 1,
                        org_id=org_id,
                        record_name=answer_title,
                        record_type=RecordType.WEBPAGE,
                        external_record_id=external_answer_id,
                        connector_name=Connectors.ZAMMAD,
                        origin=OriginTypes.CONNECTOR,
                        mime_type=MimeTypes.HTML.value,
                        weburl=f"{self.base_url}/help/en-us/{category_id}/{answer.id}" if self.base_url else None,
                        created_at=created_at,
                        updated_at=updated_at,
                        source_created_at=created_at,
                        source_updated_at=updated_at
                    )

                    # Build permissions list - KB articles are typically readable by all users
                    record_permissions: List[Permission] = []
                    
                    # Add READ permissions for all users in the organization
                    for user_external_id, user_id in user_id_map.items():
                        # Create permission edge from user to KB answer record
                        user_permission_edge: Dict[str, Any] = {
                            "_from": f"{CollectionNames.USERS.value}/{user_id}",
                            "_to": f"{CollectionNames.RECORDS.value}/{record_id}",
                            "role": "READER",
                            "type": "USER",
                            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                            "updatedAtTimestamp": get_epoch_timestamp_in_ms()
                        }
                        user_permission_edges.append(user_permission_edge)

                    # Add to records_with_permissions for on_new_records
                    records_with_permissions.append((webpage_record, record_permissions))

                    # Create isOfType edge (record -> webpage)
                    kb_answer_edge: Dict[str, Any] = {
                        "_from": f"{CollectionNames.RECORDS.value}/{record_id}",
                        "_to": f"{CollectionNames.WEBPAGES.value}/{record_id}",
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms()
                    }
                    kb_answer_edges.append(kb_answer_edge)

                    # Create belongsTo edge (record -> KB category RecordGroup)
                    if external_group_id and external_group_id in kb_category_map:
                        record_group_id: str = kb_category_map[external_group_id]
                        kb_answer_category_edge: Dict[str, Any] = {
                            "_from": f"{CollectionNames.RECORDS.value}/{record_id}",
                            "_to": f"{CollectionNames.RECORD_GROUPS.value}/{record_group_id}",
                            "entityType": "KB",
                            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                            "updatedAtTimestamp": get_epoch_timestamp_in_ms()
                        }
                        kb_answer_category_edges.append(kb_answer_category_edge)

            # Process records through data_entities_processor for storage + Kafka publishing (OUTSIDE transaction)
            # Only send NEW records to avoid re-indexing existing ones
            self.logger.info(f"\n Preparing to dispatch KB records...")
            
            new_records_with_permissions = [
                (record, perms) for record, perms in records_with_permissions 
                if record.version == 0  # version 0 means it's a new record
            ]
            
            self.logger.info(f"   New records (version==0): {len(new_records_with_permissions)}")
            self.logger.info(f"   Existing records (skipped): {len(records_with_permissions) - len(new_records_with_permissions)}")
            
            if new_records_with_permissions:
                self.logger.info("Indexing the following KB answer records:")
                for record, perms in new_records_with_permissions:
                    self.logger.info(f"Record: id={record.id}, title={getattr(record, 'record_name', None)}, external_id={record.external_record_id}")
                await self.data_entities_processor.on_new_records(new_records_with_permissions)
                self.logger.info(f"Dispatched {len(new_records_with_permissions)} NEW KB answer records for indexing")
            else:
                self.logger.info(f" No new KB records to dispatch - all {len(records_with_permissions)} KB answers already exist")

            # Create isOfType and belongsTo edges in a separate transaction
            self.logger.info(f"\n Creating edges...")
            self.logger.info(f"   isOfType edges to create: {len(kb_answer_edges)}")
            self.logger.info(f"   belongsTo edges to create: {len(kb_answer_category_edges)}")
            self.logger.info(f"   Permission edges to create: {len(user_permission_edges)}")
            
            async with self.data_store_provider.transaction() as tx_store_2:
                # Create isOfType edges (record -> webpage)
                if kb_answer_edges:
                    await tx_store_2.batch_create_edges(kb_answer_edges, CollectionNames.IS_OF_TYPE.value)
                    self.logger.info(f" Created {len(kb_answer_edges)} KB answer-webpage isOfType edges")

                # Create belongsTo edges (record -> KB category)
                if kb_answer_category_edges:
                    await tx_store_2.batch_create_edges(kb_answer_category_edges, CollectionNames.BELONGS_TO.value)
                    self.logger.info(f" Created {len(kb_answer_category_edges)} KB answer-category belongsTo edges")
                
                # Create permission edges (user -> KB answer) for visibility in "All Records"
                if user_permission_edges:
                    await tx_store_2.batch_create_edges(user_permission_edges, CollectionNames.PERMISSION.value)
                    self.logger.info(f" Created {len(user_permission_edges)} user-to-answer permission edges")
            
            self.logger.info(" KB Answers processing completed!")

        except Exception as e:
            self.logger.error(f"Error processing KB answers as records: {e}", exc_info=True)
            raise

    async def __get_ticket_content(self, ticket: ZammadTicket, articles: List[Dict[str, Any]]) -> str:
        """Combine ticket title, description, and article bodies into readable text"""
        content_parts: List[str] = []

        # Add ticket title
        if ticket.title:
            content_parts.append(f"# {ticket.title}\n")

        # Add ticket metadata
        content_parts.append(f"Status: {ticket.state or 'N/A'}")
        content_parts.append(f"Priority: {ticket.priority or 'N/A'}")
        content_parts.append(f"Group: {ticket.group or 'N/A'}\n")

        # Add articles
        if articles:
            content_parts.append("## Conversation:\n")
            for article in articles:
                sender: str = article.get('from', 'Unknown')
                body: str = article.get('body', '')
                content_parts.append(f"**From: {sender}**")
                content_parts.append(body)
                content_parts.append("")  # Empty line between articles

        return "\n".join(content_parts)

    async def __get_kb_answer_content(self, answer: ZammadKBAnswer, full_response_data: Dict[str, Any]) -> str:
        """Extract KB answer content from translations in the response assets"""
        content_parts: List[str] = []

        # Extract translations and their content from the full response data
        assets = full_response_data.get('assets', {})
        translations = assets.get('KnowledgeBaseAnswerTranslation', {})
        translation_contents = assets.get('KnowledgeBaseAnswerTranslationContent', {})

        if translations:
            for trans_id, translation in translations.items():
                title: str = translation.get('title', '')
                # Try to get body from KnowledgeBaseAnswerTranslationContent using translation id
                trans_id_str = str(trans_id)
                content_obj = translation_contents.get(trans_id_str, {})
                content_body: str = content_obj.get('body', '')
                # Fallback to translation['content']['body'] if not found
                if not content_body:
                    content_body = translation.get('content', {}).get('body', '') if isinstance(translation.get('content'), dict) else translation.get('content', '')

                if title:
                    content_parts.append(f"<h1>{title}</h1>")
                if content_body:
                    self.logger.debug("KB content: running URL absolutization")
                    content_html = self.__absolutize_html_urls(content_body)
                    self.logger.debug("KB content: running image inlining")
                    content_html = await self.__inline_images_as_base64(content_html)
                    content_parts.append(content_html)

                # Add a separator if there are multiple translations
                if len(translations) > 1:
                    content_parts.append("<hr/>")
        else:
            content_parts.append("<p>No content available</p>")
        self.logger.info(f" KB Answer content parts: {content_parts }")
        return "\n".join(content_parts)

    def __absolutize_html_urls(self, html: str) -> str:
        """Prefix self.base_url to relative src/href URLs (e.g., /api/..., #knowledge_base/...)."""
        if not html or not self.base_url:
            return html

        # Prefix src/href values that start with a root slash
        def _prefix_root(match: re.Match) -> str:
            attr = match.group('attr')
            quote = match.group('q')
            url = match.group('url')  # starts with '/'
            return f"{attr}={quote}{self.base_url}{url}{quote}"

        before = html
        html = re.sub(
            r"(?P<attr>\b(?:src|href))=(?P<q>[\"\']) (?P<url>/[^\"\']+)(?P=q)",
            _prefix_root,
            html,
            flags=re.X,
        )

        # Prefix href values that are just a hash fragment
        def _prefix_hash(match: re.Match) -> str:
            quote = match.group('q')
            frag = match.group('frag')
            return f"href={quote}{self.base_url}/#{frag}{quote}"

        html = re.sub(
            r"href=(?P<q>[\"\'])#(?P<frag>[^\"\']+)(?P=q)",
            _prefix_hash,
            html,
        )
        if before is not html:
            self.logger.debug("Absolutized relative URLs in KB HTML")
        return html

    async def __inline_images_as_base64(self, html: str) -> str:
        """Inline <img> sources as base64 by fetching with authenticated client.
        Applies caps to prevent large payloads.
        """
        if not html:
            return html
        if not self.zammad_datasource:
            return html

        max_images_to_inline = 10
        max_bytes_per_image = 2 * 1024 * 1024  # 2 MB
        allowed_content_types = {
            "image/png",
            "image/jpeg",
            "image/jpg",
            "image/gif",
            "image/webp",
        }

        img_pattern = re.compile(r"<img\s+[^>]*src=(?P<q>[\"\'])(?P<src>.*?)(?P=q)", re.IGNORECASE)
        matches = list(img_pattern.finditer(html))
        if not matches:
            return html

        zclient: ZammadClient = self.zammad_datasource.get_client()
        http_client = zclient.get_client()

        replacements: List[Tuple[Tuple[int, int], str]] = []
        inlined = 0
        self.logger.debug(f"Found {len(matches)} <img> tags; attempting to inline up to {max_images_to_inline}")
        for m in matches:
            if inlined >= max_images_to_inline:
                break
            src = m.group("src")
            if not src or src.startswith("data:"):
                self.logger.debug("Skipping image (empty or already data URI)")
                continue

            abs_url = src
            if self.base_url and src.startswith("/"):
                abs_url = f"{self.base_url}{src}"

            if self.base_url and not abs_url.startswith(self.base_url):
                self.logger.debug(f"Skipping external image: {abs_url}")
                continue

            try:
                request = HTTPRequest(url=abs_url, method="GET", headers={"Accept": "*/*"})
                response = await http_client.execute(request)
                if response.status >= 400:
                    self.logger.debug(f"Image fetch failed {response.status}: {abs_url}")
                    continue
                ctype = response.content_type
                if ctype not in allowed_content_types:
                    self.logger.debug(f"Skipping non-image or disallowed type {ctype} for {abs_url}")
                    continue
                blob = response.bytes()
                if not blob or len(blob) > max_bytes_per_image:
                    self.logger.debug(f"Skipping image due to size {len(blob) if blob else 0} bytes: {abs_url}")
                    continue
                b64 = base64.b64encode(blob).decode("ascii")
                data_uri = f"data:{ctype};base64,{b64}"
                replacements.append(((m.start("src"), m.end("src")), data_uri))
                inlined += 1
                self.logger.debug(f"Inlined image as base64 (type {ctype}, size {len(blob)} bytes)")
            except Exception as e:
                self.logger.debug(f"Error inlining image {abs_url}: {e}")
                continue

        if not replacements:
            return html

        parts: List[str] = []
        last = len(html)
        for (s, e), val in reversed(replacements):
            parts.append(html[e:last])
            parts.append(val)
            last = s
        parts.append(html[:last])
        return "".join(reversed(parts))

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
    ) -> BaseConnector:
        """Create and initialize ZammadConnector instance"""
        data_entities_processor: DataSourceEntitiesProcessor = DataSourceEntitiesProcessor(
            logger,
            data_store_provider,
            config_service
        )
        await data_entities_processor.initialize()

        return ZammadConnector(
            logger=logger,
            data_entities_processor=data_entities_processor,
            data_store_provider=data_store_provider,
            config_service=config_service
        )

