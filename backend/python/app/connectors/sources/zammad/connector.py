"""Zammad Connector Implementation"""
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
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.zammad.zammad import (
    ZammadClient,
    ZammadTokenConfig,
)
from app.sources.external.zammad.zammad import ZammadDataSource, ZammadResponse
from app.utils.time_conversion import get_epoch_timestamp_in_ms

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

            for ticket in self.tickets_data:
                external_ticket_id: str = str(ticket.id)

                # Check if record already exists (for deduplication)
                existing_record = await tx_store.get_record_by_external_id(
                    connector_name=Connectors.ZAMMAD,
                    external_id=external_ticket_id
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
                    weburl=f"{self.base_url}/ticket/zoom/{external_ticket_id}" if self.base_url else None,
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
                        "permissionType": PermissionType.READ.value,
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
                        "permissionType": PermissionType.WRITE.value,
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
                                    "permissionType": PermissionType.READ.value,
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
            if records_with_permissions:
                await self.data_entities_processor.on_new_records(records_with_permissions)
                self.logger.info(f"Dispatched {len(records_with_permissions)} records for indexing")

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
                    await tx_store_2.batch_create_edges(user_permission_edges, CollectionNames.PERMISSIONS.value)
                    self.logger.info(f"Created {len(user_permission_edges)} user-record permission edges")

        except Exception as e:
            self.logger.error(f"Error processing tickets: {e}", exc_info=True)
            raise

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Fetch and stream ticket content with articles"""
        try:
            if not self.zammad_datasource:
                await self.init()

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
            self.logger.error(f"Error streaming record: {e}")
            return StreamingResponse(
                iter([f"Error: {str(e)}"]),
                media_type=MimeTypes.PLAIN_TEXT.value
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
        """Implement incremental sync using updated_at timestamps"""
        try:
            self.logger.info("Starting Zammad incremental sync")

            # Ensure client is initialized
            if not self.zammad_datasource:
                await self.init()

            # Sync changed tickets
            changed_tickets: List[ZammadTicket] = await self._fetch_delta_tickets()
            if changed_tickets:
                self.logger.info(f"Found {len(changed_tickets)} changed tickets")
                self.tickets_data = changed_tickets
            else:
                self.logger.info("No changed tickets found")
                self.tickets_data = []

            # Sync changed users
            changed_users: List[ZammadUser] = await self._fetch_delta_users()
            if changed_users:
                self.logger.info(f"Found {len(changed_users)} changed users")
                self.users_data = changed_users
            else:
                self.logger.info("No changed users found")
                self.users_data = []

            # Only build nodes and edges if we have changes
            if changed_tickets or changed_users:
                # Fetch supporting data needed for relationships
                await self._fetch_supporting_entities()
                await self.__build_nodes_and_edges()
                self.logger.info("Zammad incremental sync completed successfully")
            else:
                self.logger.info("No changes detected, skipping node/edge creation")

        except Exception as e:
            self.logger.error(f"Error during Zammad incremental sync: {e}", exc_info=True)
            # Fallback to full sync on error
            self.logger.warning("Falling back to full sync")
            await self.run_sync()

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
            self.logger.info("Fetching Knowledge Base from Zammad")
            self.knowledge_base_data = await self.__fetch_knowledge_base()
            if not self.knowledge_base_data or len(self.knowledge_base_data) == 0:
                self.logger.debug("No Knowledge Base found or failed to fetch Knowledge Base")
            self.logger.info(f"Fetched {len(self.knowledge_base_data)} Knowledge Base from Zammad")

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

    async def __fetch_knowledge_base(self) -> List[ZammadKnowledgeBase]:
        """Fetch Zammad Knowledge Base"""
        try:
            kb_id: int = 1
            response: ZammadResponse = await self.zammad_datasource.get_knowledge_base(id=kb_id, expand="true")
            if not response.success or not response.data:
                return []

            kbs: List[ZammadKnowledgeBase] = []
            data_list: List[Dict[str, Any]] = response.data if isinstance(response.data, list) else [response.data]
            for kb_data in data_list:
                try:
                    kb: ZammadKnowledgeBase = ZammadKnowledgeBase(**kb_data)
                    kbs.append(kb)
                except Exception as e:
                    self.logger.warning(f"Failed to parse knowledge base {kb_data.get('id')}: {e}")

            return kbs
        except Exception as e:
            self.logger.error(f"Error fetching Knowledge Base: {e}", exc_info=True)
            return []

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

    # ============================================================================
    # Delta Sync Helper Methods
    # ============================================================================

    async def _fetch_delta_tickets(self) -> List[ZammadTicket]:
        """Fetch tickets changed since last sync using updated_at filter"""
        try:
            # Get last sync timestamp
            sync_state: Dict[str, Any] = await self.tickets_sync_point.read_sync_point("tickets")
            last_sync: Optional[str] = sync_state.get("last_sync_time")

            if not last_sync:
                # No previous sync, use a default timestamp (e.g., 30 days ago)
                sync_from: datetime = datetime.utcnow() - timedelta(days=30)
                self.logger.info("No previous sync found, fetching tickets from last 30 days")
            else:
                # Parse last sync time and add buffer
                sync_from_dt: datetime = datetime.fromisoformat(last_sync.replace('Z', '+00:00'))
                sync_from: datetime = sync_from_dt - timedelta(minutes=self.buffer_minutes)
                self.logger.info(f"Fetching tickets changed since {sync_from.isoformat()}")

            # Fetch changed tickets with pagination
            changed_tickets: List[ZammadTicket] = await self._fetch_paginated_tickets(sync_from)

            # Update sync state
            current_time: str = datetime.utcnow().isoformat() + "Z"
            await self.tickets_sync_point.update_sync_point("tickets", {"last_sync_time": current_time})

            return changed_tickets

        except Exception as e:
            self.logger.error(f"Error fetching delta tickets: {e}", exc_info=True)
            return []

    async def _fetch_delta_users(self) -> List[ZammadUser]:
        """Fetch users changed since last sync using updated_at filter"""
        try:
            # Get last sync timestamp
            sync_state: Dict[str, Any] = await self.users_sync_point.read_sync_point("users")
            last_sync: Optional[str] = sync_state.get("last_sync_time")

            if not last_sync:
                # No previous sync, use a default timestamp (e.g., 30 days ago)
                sync_from: datetime = datetime.utcnow() - timedelta(days=30)
                self.logger.info("No previous sync found, fetching users from last 30 days")
            else:
                # Parse last sync time and add buffer
                sync_from_dt: datetime = datetime.fromisoformat(last_sync.replace('Z', '+00:00'))
                sync_from: datetime = sync_from_dt - timedelta(minutes=self.buffer_minutes)
                self.logger.info(f"Fetching users changed since {sync_from.isoformat()}")

            # Fetch changed users with pagination
            changed_users: List[ZammadUser] = await self._fetch_paginated_users(sync_from)

            # Update sync state
            current_time: str = datetime.utcnow().isoformat() + "Z"
            await self.users_sync_point.update_sync_point("users", {"last_sync_time": current_time})

            return changed_users

        except Exception as e:
            self.logger.error(f"Error fetching delta users: {e}", exc_info=True)
            return []

    async def _fetch_paginated_tickets(self, sync_from: datetime) -> List[ZammadTicket]:
        """Fetch tickets with pagination using updated_at filter"""
        try:
            all_tickets: List[ZammadTicket] = []
            page: int = 1
            per_page: int = 100
            seen_ids: Set[int] = set()

            # Format query: updated_at:>2024-10-20T10:00:00Z
            query: str = f"updated_at:>{sync_from.strftime('%Y-%m-%dT%H:%M:%SZ')}"
            self.logger.info(f"Searching tickets with query: {query}")

            while True:
                response: ZammadResponse = await self.zammad_datasource.search_tickets(
                    query=query,
                    page=page,
                    per_page=per_page,
                    expand=True
                )

                if not response.success or not response.data:
                    break

                # Handle both dict with 'tickets' key or direct list
                tickets_data: List[Dict[str, Any]] = []
                if isinstance(response.data, dict):
                    tickets_data = response.data.get('tickets', [])
                elif isinstance(response.data, list):
                    tickets_data = response.data

                if not tickets_data:
                    break

                # Parse and deduplicate tickets
                for ticket_data in tickets_data:
                    try:
                        ticket: ZammadTicket = ZammadTicket(**ticket_data)
                        if ticket.id not in seen_ids:
                            seen_ids.add(ticket.id)
                            all_tickets.append(ticket)
                    except Exception as e:
                        self.logger.warning(f"Failed to parse ticket {ticket_data.get('id')}: {e}")

                # Check if there are more pages
                if len(tickets_data) < per_page:
                    break

                page += 1

                # Safety limit
                if page > THRESHOLD_PAGINATION_LIMIT:
                    self.logger.warning("Hit page limit for ticket search")
                    break

            self.logger.info(f"Fetched {len(all_tickets)} changed tickets")
            return all_tickets

        except Exception as e:
            self.logger.error(f"Error in paginated ticket fetch: {e}", exc_info=True)
            return []

    async def _fetch_paginated_users(self, sync_from: datetime) -> List[ZammadUser]:
        """Fetch users with updated_at filter (Note: Zammad user search doesn't support pagination)"""
        try:
            all_users: List[ZammadUser] = []

            # Format query: updated_at:>2024-10-20T10:00:00Z
            query: str = f"updated_at:>{sync_from.strftime('%Y-%m-%dT%H:%M:%SZ')}"
            self.logger.info(f"Searching users with query: {query}")

            # Note: Zammad's search_users API only accepts query and limit (no pagination)
            # Using a large limit to get all results
            response: ZammadResponse = await self.zammad_datasource.search_users(
                query=query,
                limit=1000  # Large limit to get all changed users
            )

            if not response.success:
                self.logger.warning(f"User search failed: {response.error or response.message}")
                return []

            if not response.data:
                self.logger.debug("No user data in response")
                return []

            # Handle both dict with 'users' key or direct list
            users_data: List[Dict[str, Any]] = []
            if isinstance(response.data, dict):
                users_data = response.data.get('users', [])
            elif isinstance(response.data, list):
                users_data = response.data

            if not users_data:
                self.logger.info("No users found matching the query")
                return []

            # Parse users
            for user_data in users_data:
                try:
                    user: ZammadUser = ZammadUser(**user_data)
                    all_users.append(user)
                except Exception as e:
                    self.logger.warning(f"Failed to parse user {user_data.get('id')}: {e}")

            self.logger.info(f"Fetched {len(all_users)} changed users")
            return all_users

        except Exception as e:
            self.logger.error(f"Error in user fetch: {e}", exc_info=True)
            return []

    async def _fetch_supporting_entities(self) -> None:
        """Fetch supporting entities needed for relationships"""
        try:
            # Fetch organizations if we don't have them
            if not self.sub_orgs_data:
                self.sub_orgs_data = await self.__fetch_sub_organizations()

            # Fetch roles if we don't have them
            if not self.roles_data:
                self.roles_data = await self.__fetch_roles()

            # Fetch groups if we don't have them
            if not self.groups_data:
                self.groups_data = await self.__fetch_groups()

        except Exception as e:
            self.logger.error(f"Error fetching supporting entities: {e}", exc_info=True)

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

