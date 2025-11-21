"""Zammad Connector Implementation"""
import base64
import re
from datetime import datetime, timezone
from logging import Logger
from typing import Any, Dict, List, Optional, Tuple, Union
from uuid import uuid4

from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
    RecordRelations,
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
from app.connectors.sources.zammad.common.apps import ZammadApp
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.zammad.zammad import ZammadClient, ZammadTokenConfig
from app.sources.external.zammad.zammad import ZammadDataSource, ZammadResponse
from app.utils.time_conversion import get_epoch_timestamp_in_ms

THRESHOLD_PAGINATION_LIMIT: int = 100
HTTP_ERROR_STATUS_CODE: int = 400

# Zammad role IDs
ZAMMAD_ROLE_ADMIN: int = 1
ZAMMAD_ROLE_AGENT: int = 2
ZAMMAD_ROLE_CUSTOMER: int = 3

# File size conversion constants
BYTES_PER_KB: int = 1024

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
            "https://docs.zammad.org/en/latest/api/intro.html",
            "setup"
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
        self.sub_orgs_data: List[Dict[str, Any]] = []
        self.users_data: List[Dict[str, Any]] = []
        self.groups_data: List[Dict[str, Any]] = []
        self.tickets_data: List[Dict[str, Any]] = []
        self.knowledge_base_data: List[Dict[str, Any]] = []
        self.kb_categories_data: List[Dict[str, Any]] = []
        self.kb_answers_data: List[Dict[str, Any]] = []
        self.kb_full_response_data: Dict[str, Any] = {}
        self.roles_data: List[Dict[str, Any]] = []

        self._init_sync_points()

        # Buffer time for catching race conditions (5 minutes)
        self.buffer_minutes: int = 5

    def _init_sync_points(self) -> None:

        org_id = self.data_entities_processor.org_id

        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_name=Connectors.ZAMMAD,
                org_id=org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider
            )

        self.tickets_sync_point = _create_sync_point(SyncDataPointType.RECORDS)
        self.users_sync_point = _create_sync_point(SyncDataPointType.USERS)
        self.webhook_sync_point = _create_sync_point(SyncDataPointType.RECORDS)
        self.kb_categories_sync_point = _create_sync_point(SyncDataPointType.RECORD_GROUPS)
        self.kb_answers_sync_point = _create_sync_point(SyncDataPointType.RECORDS)

    async def init(self) -> None:
        """Initialize Zammad client using token from config"""
        try:
            config: Optional[Dict[str, Any]] = await self.config_service.get_config("/services/connectors/zammad/config")

            if not config:
                raise ValueError("Zammad configuration not found")

            auth_config: Dict[str, Any] = config.get("auth", {})
            self.base_url = auth_config.get("base_url") or auth_config.get("baseUrl")
            token: Optional[str] = auth_config.get("token")

            if not self.base_url or not token:
                raise ValueError("Zammad base_url and token are required")

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
        """Main sync flow - automatically chooses between full sync and incremental sync"""
        try:
            if not self.zammad_datasource:
                await self.init()

            try:
                last_sync_data = await self.tickets_sync_point.read_sync_point("tickets")
                last_sync_time = last_sync_data.get("last_sync_time") if last_sync_data else None
            except Exception as sync_point_error:
                self.logger.warning(f"Could not read sync point: {sync_point_error}")
                last_sync_time = None

            if last_sync_time:
                self.logger.info("Running incremental sync")
                await self.run_incremental_sync()
            else:
                self.logger.info("Running full sync")
                await self._fetch_entities()
                await self._build_nodes_and_edges()
                await self._create_initial_sync_points()

            self.logger.info("Zammad sync completed successfully")

        except Exception as e:
            self.logger.error(f"Error during Zammad sync: {e}", exc_info=True)
            raise

    async def _build_nodes_and_edges(self) -> None:
        """Build nodes and edges for the Zammad connector"""
        try:
            org_id: Optional[str] = self.data_entities_processor.org_id
            if not org_id:
                raise ValueError("Organization ID not found")

            apps: List[Dict[str, Any]] = await self.data_store_provider.arango_service.get_org_apps(org_id)
            app_key: Optional[str] = next((a.get("_key") for a in apps if a.get("type") == Connectors.ZAMMAD.value), None)

            if not app_key:
                self.logger.warning("Zammad app not found for organization")
                return

            if not self.roles_data:
                self.roles_data = await self._fetch_roles()

            if not self.groups_data:
                self.groups_data = await self._fetch_groups()

            all_users_response: ZammadResponse = await self.zammad_datasource.list_users()
            user_id_email_map: Dict[int, str] = {}

            if all_users_response.success and all_users_response.data:
                for user_data in all_users_response.data:
                    try:
                        if user_data.get("email"):
                            user_id_email_map[user_data.get("id")] = user_data.get("email")
                    except Exception as e:
                        self.logger.warning(f"Failed to parse user {user_data.get('id')}: {e}")

            async with self.data_store_provider.transaction() as tx_store:
                await self._create_role_nodes(tx_store)

            sub_org_map: Dict[str, str] = await self._process_sub_organizations(org_id, app_key)
            user_id_map: Dict[str, str] = await self._process_users(org_id, app_key, sub_org_map)
            groups_map: Dict[str, str] = await self._process_groups(org_id, app_key)
            await self._create_group_app_edges(app_key, groups_map, sub_org_map)

            async with self.data_store_provider.transaction() as tx_store:
                await self._process_tickets(tx_store, org_id, sub_org_map, user_id_map)

            if self.kb_categories_data or self.kb_answers_data:
                await self._process_knowledge_base(org_id, app_key)

        except Exception as e:
            self.logger.error(f"Error building nodes and edges: {e}", exc_info=True)
            raise

    async def _create_role_nodes(self, tx_store: TransactionStore) -> None:
        """Create AppUserGroup nodes for Zammad roles (used for KB category permissions)."""
        try:
            if not self.roles_data:
                self.logger.warning("No roles data available")
                return

            role_groups: List[AppUserGroup] = []
            for role in self.roles_data:
                role_id = role.get("id")
                role_name = role.get("name", "").lower().replace(' ', '_')
                role_key: str = f"zammad_{role_name}"

                role_group = AppUserGroup(
                    id=role_key,
                    source_user_group_id=str(role_id),
                    app_name=Connectors.ZAMMAD,
                    org_id=self.data_entities_processor.org_id,
                    name=role.get("name") or role_key,
                    description=role.get("note") or f"Zammad Role: {role.get('name')}",
                    active=role.get("active", False)
                )
                role_groups.append(role_group)

            await tx_store.batch_upsert_user_groups(role_groups)
            self.logger.info(f"Stored {len(role_groups)} role groups")


        except Exception as e:
            self.logger.error(f"Error creating role nodes: {e}", exc_info=True)
            raise

    async def _process_groups(self, org_id: str, app_key: str) -> Dict[str, str]:
        """Process groups and return mapping of Zammad group ID to internal AppUserGroup ID."""
        try:

            if not self.groups_data:
                self.logger.warning("No groups data available")
                return {}

            groups_map: Dict[str, str] = {}
            groups_with_members: List[Tuple[AppUserGroup, List[AppUser]]] = []
            external_id_to_group_id: Dict[str, str] = {}

            user_id_to_email: Dict[int, str] = {}
            for user in self.users_data:
                user_id = user.get("id")
                email = user.get("email")
                if user_id and email:
                    user_id_to_email[user_id] = email

            async with self.data_store_provider.transaction() as tx_store:
                for group in self.groups_data:
                    group_id_value = group.get("id")
                    external_group_id: str = f"group_{group_id_value}"

                    group_name = group.get("name") or f"Group {group_id_value}"
                    user_group = AppUserGroup(
                        source_user_group_id=external_group_id,
                        app_name=Connectors.ZAMMAD,
                        name=group_name,
                        active=group.get("active", True),
                        description=group.get("note") or ""
                    )

                    members: List[AppUser] = []
                    user_ids = group.get("user_ids", [])

                    for user_id in user_ids:
                        email = user_id_to_email.get(user_id)
                        if email:
                            db_user = await tx_store.get_user_by_email(email)
                            if db_user:
                                members.append(db_user)
                            else:
                                self.logger.warning(f"User {email} (ID: {user_id}) not found in DB for group {group_name}")

                    groups_with_members.append((user_group, members))
                    external_id_to_group_id[external_group_id] = str(group_id_value)

            if groups_with_members:
                await self.data_entities_processor.on_new_user_groups(groups_with_members)

                for user_group, _ in groups_with_members:
                    original_group_id = external_id_to_group_id.get(user_group.source_user_group_id)
                    if original_group_id and user_group.id:
                        groups_map[original_group_id] = user_group.id

            return groups_map

        except Exception as e:
            self.logger.error(f"Error processing groups: {e}", exc_info=True)
            raise

    async def _create_group_app_edges(
        self,
        app_key: str,
        groups_map: Optional[Dict[str, str]] = None,
        orgs_map: Optional[Dict[str, str]] = None
    ) -> None:

        try:
            group_app_edges: List[Dict[str, Any]] = []

            if groups_map:
                for external_id, user_group_id in groups_map.items():
                    edge = {
                        "_from": f"{CollectionNames.GROUPS.value}/{user_group_id}",
                        "_to": f"{CollectionNames.APPS.value}/{app_key}",
                        "entityType": "GROUP",
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms()
                    }
                    group_app_edges.append(edge)

            if orgs_map:
                for external_id, user_group_id in orgs_map.items():
                    edge = {
                        "_from": f"{CollectionNames.GROUPS.value}/{user_group_id}",
                        "_to": f"{CollectionNames.APPS.value}/{app_key}",
                        "entityType": "ORGANIZATION",
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms()
                    }
                    group_app_edges.append(edge)

            if group_app_edges:
                async with self.data_store_provider.transaction() as tx_store:
                    await tx_store.batch_create_edges(
                        group_app_edges,
                        CollectionNames.BELONGS_TO.value
                    )

        except Exception as e:
            self.logger.error(f"Error creating group/org app edges: {e}", exc_info=True)
            raise

    async def _process_sub_organizations(self, org_id: str, app_key: str, all_users_data: Optional[List[Dict[str, Any]]] = None) -> Dict[str, str]:
        """
        Process sub-organizations as AppUserGroup.
        """
        try:

            users_to_check = all_users_data if all_users_data is not None else self.users_data

            sub_org_map: Dict[str, str] = {}
            orgs_with_members: List[Tuple[AppUserGroup, List[AppUser]]] = []
            external_id_to_org_id: Dict[str, str] = {}

            user_id_to_email: Dict[int, str] = {}
            for user in users_to_check:
                user_id = user.get("id")
                email = user.get("email")
                if user_id and email:
                    user_id_to_email[user_id] = email

            async with self.data_store_provider.transaction() as tx_store:
                for sub_org in self.sub_orgs_data:
                    org_id_value = sub_org.get("id")
                    external_org_id: str = f"org_{org_id_value}"

                    created_at: int = self._parse_datetime_to_timestamp(sub_org.get("created_at"))

                    org_name = sub_org.get("name") or f"Organization {org_id_value}"
                    user_group = AppUserGroup(
                        source_user_group_id=external_org_id,
                        app_name=Connectors.ZAMMAD,
                        name=org_name,
                        description=sub_org.get("note") or f"Zammad Organization: {org_name}",
                        created_at_timestamp=created_at
                    )

                    members: List[AppUser] = []
                    member_ids = sub_org.get("member_ids", [])
                    secondary_member_ids = sub_org.get("secondary_member_ids", [])
                    all_member_ids = set(member_ids + secondary_member_ids)

                    for user_id in all_member_ids:
                        email = user_id_to_email.get(user_id)
                        if email:
                            db_user = await tx_store.get_user_by_email(email)
                            if db_user:
                                members.append(db_user)
                            else:
                                self.logger.warning(f"User {email} (ID: {user_id}) not found in DB for org {org_name}")

                    orgs_with_members.append((user_group, members))
                    external_id_to_org_id[external_org_id] = str(org_id_value)
            if orgs_with_members:
                await self.data_entities_processor.on_new_user_groups(orgs_with_members)

                for user_group, _ in orgs_with_members:
                    original_org_id = external_id_to_org_id.get(user_group.source_user_group_id)
                    if original_org_id and user_group.id:
                        sub_org_map[original_org_id] = user_group.id
                    else:
                        self.logger.warning(f"Organization '{user_group.name}' (external_id: {user_group.source_user_group_id}) missing mapping or ID")

            return sub_org_map

        except Exception as e:
            self.logger.error(f"Error processing sub-organizations: {e}", exc_info=True)
            raise

    async def _process_users(self, org_id: str, app_key: str, sub_org_map: Dict[str, str]) -> Dict[str, str]:
        """Process users and create edges. Returns mapping of external_user_id -> internal_user_id."""
        try:

            app_users: List[AppUser] = []
            user_metadata: List[Dict[str, Any]] = []
            user_id_map: Dict[str, str] = {}

            for user in self.users_data:
                external_user_id: str = str(user.get("id"))
                email: str = user.get("email")
                if not email:
                    self.logger.warning(f"Skipping user without email: {user.get('id')}")
                    continue

                full_name: str = f"{user.get('firstname')} {user.get('lastname')}".strip()

                created_at: int = self._parse_datetime_to_timestamp(user.get("created_at"))
                updated_at: int = self._parse_datetime_to_timestamp(user.get("updated_at"))
                app_user: AppUser = AppUser(
                    app_name=Connectors.ZAMMAD,
                    source_user_id=external_user_id,
                    org_id=org_id,
                    email=email,
                    full_name=full_name if full_name else email,
                    is_active=user.get("active"),
                    created_at=created_at,
                    updated_at=updated_at,
                    source_created_at=created_at,
                    source_updated_at=updated_at
                )
                app_users.append(app_user)

                user_metadata.append({
                    'external_user_id': external_user_id,
                    'email': email,
                    'role_keys': self._get_user_role_keys(user),
                    'organization_id': user.get("organization_id")
                })

            if app_users:
                await self.data_entities_processor.on_new_app_users(app_users)
                self.logger.info(f"Processed {len(app_users)} users through on_new_app_users")

            async with self.data_store_provider.transaction() as tx_store:
                user_role_edges: List[Dict[str, Any]] = []
                user_role_permission_edges: List[Dict[str, Any]] = []

                for metadata in user_metadata:
                    db_user = await tx_store.get_user_by_email(metadata['email'])
                    if not db_user:
                        self.logger.warning(f"User {metadata['email']} not found in DB after on_new_app_users")
                        continue

                    user_id: str = db_user.id
                    user_id_map[metadata['external_user_id']] = user_id

                    from_collection = f"{CollectionNames.USERS.value}/{user_id}"

                    role_keys = metadata['role_keys']
                    for role_key in role_keys:
                        to_collection = f"{CollectionNames.GROUPS.value}/{role_key}"

                        user_role_edge: Dict[str, Any] = {
                            "_from": from_collection,
                            "_to": to_collection,
                            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                            "updatedAtTimestamp": get_epoch_timestamp_in_ms()
                        }
                        user_role_edges.append(user_role_edge)

                        permission = Permission(
                            external_id=None,
                            email=metadata['email'],
                            type=PermissionType.READ,
                            entity_type=EntityType.USER
                        )
                        permission_edge = permission.to_arango_permission(from_collection, to_collection)
                        user_role_permission_edges.append(permission_edge)

                if user_role_edges:
                    try:
                        await tx_store.batch_create_edges(user_role_edges, CollectionNames.IS_OF_TYPE.value)
                    except Exception as e:
                        self.logger.error(f"Failed to create user-role IS_OF_TYPE edges: {e}", exc_info=True)

                if user_role_permission_edges:
                    try:
                        await tx_store.batch_create_edges(user_role_permission_edges, CollectionNames.PERMISSION.value)
                    except Exception as e:
                        self.logger.error(f"Failed to create user-role PERMISSION edges: {e}", exc_info=True)

            return user_id_map

        except Exception as e:
            self.logger.error(f"Error processing users: {e}", exc_info=True)
            raise

    def _get_user_role_keys(self, user: Dict[str, Any]) -> List[str]:
        """
        Get ALL role keys for a user (users can have multiple roles).
        """

        role_ids: List[int] = user.get("role_ids", [])

        if not role_ids:
            self.logger.warning(f"User {user.get('id')} has no role_ids, defaulting to 'customer'")
            return ["zammad_customer"]

        role_keys: List[str] = []

        if self.roles_data:
            for role_id in role_ids:
                role: Optional[Dict[str, Any]] = next((r for r in self.roles_data if r.get("id") == role_id), None)
                if role and role.get("active", False):
                    role_name = role.get("name", "").lower().replace(' ', '_')
                    role_key = f"zammad_{role_name}"
                    role_keys.append(role_key)
                else:
                    self.logger.warning(f"Role ID {role_id} not found or inactive for user {user.get('id')}")

            if role_keys:
                return role_keys

        # Fallback to hardcoded mapping if roles_data is not available
        self.logger.warning(f"Using fallback role mapping for user {user.get('id')} with role_ids: {role_ids}")
        for role_id in role_ids:
            if role_id == ZAMMAD_ROLE_ADMIN:
                role_keys.append("zammad_admin")
            elif role_id == ZAMMAD_ROLE_AGENT:
                role_keys.append("zammad_agent")
            elif role_id == ZAMMAD_ROLE_CUSTOMER:
                role_keys.append("zammad_customer")
            else:
                role_keys.append(f"zammad_role_{role_id}")

        return role_keys if role_keys else ["zammad_customer"]

    def _parse_datetime_to_timestamp(self, datetime_value: Optional[Union[str, datetime, int, float]]) -> int:
        """
        Parse datetime value (string or datetime object) to epoch timestamp in milliseconds.
        """
        if not datetime_value:
            return get_epoch_timestamp_in_ms()

        try:
            if isinstance(datetime_value, datetime):
                return int(datetime_value.timestamp() * 1000)

            if isinstance(datetime_value, str):
                dt = self._parse_datetime_string(datetime_value)
                if dt:
                    return int(dt.timestamp() * 1000)

            if isinstance(datetime_value, (int, float)):
                return int(datetime_value)

        except Exception as e:
            self.logger.warning(f"Failed to parse datetime '{datetime_value}': {e}")

        return get_epoch_timestamp_in_ms()

    def _parse_datetime_string(self, datetime_str: Optional[str]) -> Optional[datetime]:
        """
        Parse datetime string to datetime object.

        """
        if not datetime_str:
            return None

        if isinstance(datetime_str, datetime):
            return datetime_str

        try:
            if ' ' in datetime_str and 'T' not in datetime_str:
                dt_str = datetime_str.replace(' ', 'T')
                if '+' not in dt_str and 'Z' not in dt_str and dt_str[-1].isdigit():
                    dt_str = dt_str + '+00:00'
                else:
                    dt_str = dt_str.replace('Z', '+00:00')
                return datetime.fromisoformat(dt_str)
            else:
                dt_str = datetime_str.replace('Z', '+00:00')
                return datetime.fromisoformat(dt_str)
        except Exception as e:
            self.logger.warning(f"Failed to parse datetime string '{datetime_str}': {e}")
            return None

    def _extract_ticket_permissions(
        self,
        ticket: Dict[str, Any],
        user_id_map: Dict[str, str],
        sub_org_map: Dict[str, str],
        all_users_data: Optional[List[Dict[str, Any]]] = None
    ) -> List[Permission]:
        """
        Extract permissions from ticket data.

        """
        permissions: List[Permission] = []
        processed_user_ids: set = set()
        users_to_check = all_users_data if all_users_data else self.users_data

        def find_user_email(user_id: str) -> Optional[str]:
            """Helper to find user email by Zammad user ID"""
            for user in users_to_check:
                if str(user.get("id")) == user_id:
                    return user.get("email")
            return None

        customer_id = str(ticket.get("customer_id")) if ticket.get("customer_id") else None
        if customer_id:
            customer_email = find_user_email(customer_id)
            if customer_email:
                permissions.append(Permission(
                    external_id=customer_id,
                    email=customer_email,
                    type=PermissionType.READ,
                    entity_type=EntityType.USER
                ))
                processed_user_ids.add(customer_id)

        owner_id = str(ticket.get("owner_id")) if ticket.get("owner_id") else None
        if owner_id and owner_id not in processed_user_ids:
            owner_email = find_user_email(owner_id)
            if owner_email:
                permissions.append(Permission(
                    external_id=owner_id,
                    email=owner_email,
                    type=PermissionType.OWNER,
                    entity_type=EntityType.USER
                ))
                processed_user_ids.add(owner_id)

        created_by_id = str(ticket.get("created_by_id")) if ticket.get("created_by_id") else None
        if created_by_id and created_by_id not in processed_user_ids:
            created_by_email = find_user_email(created_by_id)
            if created_by_email:
                permissions.append(Permission(
                    external_id=created_by_id,
                    email=created_by_email,
                    type=PermissionType.READ,
                    entity_type=EntityType.USER
                ))
                processed_user_ids.add(created_by_id)

        group_id = ticket.get("group_id")
        if group_id:
            external_group_id = f"group_{group_id}"
            permissions.append(Permission(
                external_id=external_group_id,
                email=None,
                type=PermissionType.READ,
                entity_type=EntityType.GROUP
            ))

        ticket_org_id = str(ticket.get("organization_id")) if ticket.get("organization_id") else None
        if ticket_org_id:
            external_org_id = f"org_{ticket_org_id}"
            permissions.append(Permission(
                external_id=external_org_id,
                email=None,
                type=PermissionType.READ,
                entity_type=EntityType.GROUP
            ))

        return permissions

    def _extract_category_permissions(self, category: Dict[str, Any]) -> List[Permission]:
        """Extract role-based permissions from KB category's permissions_effective for RecordGroup."""
        permissions: List[Permission] = []
        perms_effective = category.get("permissions_effective", [])

        for perm in perms_effective:
            role_id = perm.get("role_id")
            access = perm.get("access")

            if not role_id:
                continue

            if access == "none":
                continue

            perm_type = PermissionType.OWNER if access == "editor" else PermissionType.READ

            permissions.append(Permission(
                external_id=str(role_id),
                email=None,
                type=perm_type,
                entity_type=EntityType.GROUP
            ))

        return permissions

    def _extract_kb_permissions(self, answer: Dict[str, Any], category_permissions: Dict[str, List[Dict[str, Any]]]) -> List[Permission]:
        """Extract permissions for KB answer based on visibility state. Draft: editors only; others: all roles."""
        permissions: List[Permission] = []

        published_at = answer.get("published_at")
        internal_at = answer.get("internal_at")
        archived_at = answer.get("archived_at")

        is_draft = not published_at and not internal_at and not archived_at

        category_id = str(answer.get("category_id"))
        perms_effective = category_permissions.get(category_id, [])

        for perm in perms_effective:
            role_id = perm.get("role_id")
            access = perm.get("access")

            if not role_id:
                continue

            if access == "none":
                continue

            if is_draft and access != "editor":
                continue

            perm_type = PermissionType.OWNER if access == "editor" else PermissionType.READ

            permissions.append(Permission(
                external_id=str(role_id),
                email=None,
                type=perm_type,
                entity_type=EntityType.GROUP
            ))

        return permissions

    async def _process_tickets(self, tx_store: TransactionStore, org_id: str, sub_org_map: Dict[str, str], user_id_map: Dict[str, str], all_users_data: Optional[List[Dict[str, Any]]] = None) -> None:
        """Process tickets as Records with TicketRecords and create edges"""
        try:

            records_with_permissions: List[Tuple[TicketRecord, List[Permission]]] = []
            attachment_records_with_permissions: List[Tuple[FileRecord, List[Permission]]] = []
            all_attachment_relation_edges: List[Dict[str, Any]] = []


            for ticket in self.tickets_data:
                external_ticket_id: str = str(ticket.get("id"))

                existing_record = await tx_store.get_record_by_external_id(
                    connector_name=Connectors.ZAMMAD,
                    external_id=external_ticket_id,
                    record_type="TICKET"
                )
                record_id: str = existing_record.id if existing_record else str(uuid4())
                is_new: bool = existing_record is None
                created_at: int = self._parse_datetime_to_timestamp(ticket.get("created_at"))
                updated_at: int = self._parse_datetime_to_timestamp(ticket.get("updated_at"))

                state_map = {
                    1: "closed",
                    2: "new",
                    3: "open",
                    4: "pending close",
                    5: "pending reminder",
                }
                status_value: str = state_map.get(ticket.get('state_id'), f"state_{ticket.get('state_id')}") if ticket.get('state_id') else "Unknown"

                priority_map = {
                    1: "low",
                    2: "normal",
                    3: "high"
                }
                priority_value: str = priority_map.get(ticket.get('priority_id'), f"priority_{ticket.get('priority_id')}") if ticket.get('priority_id') else "Unknown"

                ticket_record: TicketRecord = TicketRecord(
                    id=record_id,
                    version=0 if is_new else existing_record.version + 1,
                    org_id=org_id,
                    record_name=ticket.get('title') or f"Ticket #{external_ticket_id}",
                    record_type=RecordType.TICKET,
                    external_record_id=external_ticket_id,
                    connector_name=Connectors.ZAMMAD,
                    origin=OriginTypes.CONNECTOR,
                    mime_type=MimeTypes.HTML.value,
                    weburl=f"{self.base_url}/#ticket/zoom/{external_ticket_id}" if self.base_url else None,
                    created_at=created_at,
                    updated_at=updated_at,
                    source_created_at=created_at,
                    source_updated_at=updated_at,
                    summary=ticket.get("title") or "",
                    description=ticket.get("note") or "",
                    status=status_value,
                    priority=priority_value,
                    assignee=str(ticket.get("owner_id")) if ticket.get("owner_id") else None,
                    reporter_email=None,
                    assignee_email=None
                )

                record_permissions: List[Permission] = self._extract_ticket_permissions(
                    ticket=ticket,
                    user_id_map=user_id_map,
                    sub_org_map=sub_org_map,
                    all_users_data=all_users_data
                )

                records_with_permissions.append((ticket_record, record_permissions))

                try:
                    articles_response: ZammadResponse = await self.zammad_datasource.list_ticket_articles(ticket.get("id"))
                    if articles_response.success and articles_response.data:
                        articles: List[Dict[str, Any]] = articles_response.data if isinstance(articles_response.data, list) else []

                        if articles:
                            attachment_records = self._create_ticket_attachment_records(
                                ticket_id=ticket.get("id"),
                                articles=articles,
                                org_id=org_id,
                                created_at=created_at,
                                updated_at=updated_at
                            )

                            if not is_new:
                                filtered_attachment_records = []
                                for file_record, _ in attachment_records:
                                    existing_attachment = await tx_store.get_record_by_external_id(
                                        connector_name=Connectors.ZAMMAD,
                                        external_id=file_record.external_record_id,
                                        record_type="FILE"
                                    )
                                    if existing_attachment is None:
                                        filtered_attachment_records.append((file_record, _))

                                attachment_records = filtered_attachment_records

                            for file_record, record in attachment_records:
                                attachment_permissions: List[Permission] = record_permissions.copy()
                                attachment_records_with_permissions.append((file_record, attachment_permissions))

                                attachment_relation_edge: Dict[str, Any] = {
                                    "_from": f"{CollectionNames.RECORDS.value}/{record_id}",
                                    "_to": f"{CollectionNames.RECORDS.value}/{file_record.id}",
                                    "relationType": RecordRelations.ATTACHMENT.value,
                                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                                    "updatedAtTimestamp": get_epoch_timestamp_in_ms()
                                }
                                all_attachment_relation_edges.append(attachment_relation_edge)

                except Exception as e:
                    self.logger.error(f"Error processing attachments for ticket {external_ticket_id}: {e}", exc_info=True)

            new_records_with_permissions = [
                (record, perms) for record, perms in records_with_permissions
                if record.version == 0  # version 0 means it's a new record
            ]

            updated_records = [
                record for record, perms in records_with_permissions
                if record.version > 0  # version > 0 means it's an updated record
            ]

            if new_records_with_permissions:
                await self.data_entities_processor.on_new_records(new_records_with_permissions)
                self.logger.info(f"Dispatched {len(new_records_with_permissions)} NEW ticket records")

            if updated_records:
                async with self.data_store_provider.transaction() as tx_store_update:
                    ticket_records_to_update = [record for record in updated_records if isinstance(record, TicketRecord)]
                    if ticket_records_to_update:
                        await tx_store_update.batch_upsert_records(ticket_records_to_update)

                for record in updated_records:
                    await self.data_entities_processor.on_record_content_update(record)
                self.logger.info(f"Dispatched {len(updated_records)} UPDATED ticket records")

            if attachment_records_with_permissions:
                await self.data_entities_processor.on_new_records(attachment_records_with_permissions)

            async with self.data_store_provider.transaction() as tx_store_2:
                if all_attachment_relation_edges:
                    await tx_store_2.batch_create_edges(
                        all_attachment_relation_edges,
                        CollectionNames.RECORD_RELATIONS.value
                    )

        except Exception as e:
            self.logger.error(f"Error processing tickets: {e}", exc_info=True)
            raise

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Fetch and stream record content (tickets, KB answers, or file attachments)"""
        try:
            if not self.zammad_datasource:
                await self.init()

            # Check record type to determine how to stream
            if record.record_type == RecordType.TICKET:
                return await self._stream_ticket(record)
            elif record.record_type == RecordType.WEBPAGE:
                return await self._stream_kb_answer(record)
            elif record.record_type == RecordType.FILE:
                return await self._stream_file_attachment(record)
            else:
                return StreamingResponse(
                    iter([f"Unsupported record type: {record.record_type}"]),
                    media_type=MimeTypes.PLAIN_TEXT.value
                )

        except Exception as e:
            self.logger.error(f"Error streaming record: {e}", exc_info=True)
            return StreamingResponse(
                iter(["An error occurred while processing the record."]),
                media_type=MimeTypes.PLAIN_TEXT.value
            )

    async def _stream_ticket(self, record: Record) -> StreamingResponse:
        """Stream ticket content with articles"""
        try:
            ticket_id: int = int(record.external_record_id)

            ticket_response: ZammadResponse = await self.zammad_datasource.get_ticket(ticket_id)
            if not ticket_response.success or not ticket_response.data:
                return StreamingResponse(
                    iter(["<p>Ticket not found</p>"]),
                    media_type=MimeTypes.HTML.value
                )

            ticket_dict: Dict[str, Any] = ticket_response.data if isinstance(ticket_response.data, dict) else {}
            ticket: Dict[str, Any] = ticket_dict

            articles_response: ZammadResponse = await self.zammad_datasource.list_ticket_articles(ticket_id)
            articles: List[Dict[str, Any]] = []
            if articles_response.success and articles_response.data:
                articles = articles_response.data if isinstance(articles_response.data, list) else []

            content: str = await self._get_ticket_content(ticket, articles)

            return StreamingResponse(
                iter([content]),
                media_type=MimeTypes.HTML.value,
                headers={}
            )

        except Exception as e:
            self.logger.error(f"Error streaming ticket: {e}", exc_info=True)
            return StreamingResponse(
                iter(["<p>An error occurred while processing the ticket.</p>"]),
                media_type=MimeTypes.HTML.value
            )

    async def _stream_kb_answer(self, record: Record) -> StreamingResponse:
        """Stream KB answer content"""
        try:
            answer_id: int = int(record.external_record_id)
            kb_id: int = 1

            answer_response: ZammadResponse = await self.zammad_datasource.get_kb_answer(
                kb_id=kb_id,
                id=answer_id,
                full=True,
                include_contents=True
            )
            if not answer_response.success or not answer_response.data:
                return StreamingResponse(
                    iter(["KB Answer not found"]),
                    media_type=MimeTypes.HTML.value
                )

            answer_dict = answer_response.data.get('assets', {}).get('KnowledgeBaseAnswer', {}).get(str(answer_id), {})
            if not answer_dict:
                return StreamingResponse(
                    iter(["KB Answer data not found in response"]),
                    media_type=MimeTypes.HTML.value
                )

            answer: Dict[str, Any] = answer_dict
            content: str = await self._get_kb_answer_content(answer, answer_response.data)

            return StreamingResponse(
                iter([content]),
                media_type=MimeTypes.HTML.value,
                headers={}
            )

        except Exception as e:
            self.logger.error(f"Error streaming KB answer: {e}", exc_info=True)
            return StreamingResponse(
                iter(["An error occurred while processing the KB answer."]),
                media_type=MimeTypes.HTML.value
            )

    async def _stream_file_attachment(self, record: Record) -> StreamingResponse:
        """Stream file attachment content for indexing"""
        try:
            attachment_id: int = int(record.external_record_id)
            attachment_response: ZammadResponse = await self.zammad_datasource.download_attachment(attachment_id)

            if not attachment_response.success or attachment_response.data is None:
                self.logger.warning(f"Attachment {attachment_id} not found or download failed")
                return StreamingResponse(
                    iter([b"Attachment not found"]),
                    media_type=MimeTypes.PLAIN_TEXT.value
                )

            mime_type = record.mime_type or MimeTypes.BIN.value

            return StreamingResponse(
                iter([attachment_response.data]),  # Binary content
                media_type=mime_type,
                headers={}
            )

        except Exception as e:
            self.logger.error(f"Error streaming file attachment: {e}", exc_info=True)
            return StreamingResponse(
                iter([b"An error occurred while processing the attachment."]),
                media_type=MimeTypes.PLAIN_TEXT.value
            )

    async def run_incremental_sync(self) -> None:
        """
        Uses updated_at timestamps and sync points to fetch only changed data.
        """
        try:
            self.logger.info("Starting Zammad incremental sync")

            # Ensure client is initialized
            if not self.zammad_datasource:
                await self.init()

            # Fetch entities with incremental filtering
            await self._fetch_entities_incremental()

            # Build nodes and edges with incremental data
            await self._build_nodes_and_edges_incremental()

            # Update sync points after successful sync
            await self._update_sync_points()

            self.logger.info("Zammad incremental sync completed successfully")

        except Exception as e:
            self.logger.error(f"Error during Zammad incremental sync: {e}", exc_info=True)
            raise

    async def _fetch_entities_incremental(self) -> None:
        """Fetch entities incrementally - always fetches all users/groups/orgs/roles for edge processing, only tickets/KB are incremental"""
        try:
            self.kb_categories_data = []
            self.kb_answers_data = []
            self.kb_full_response_data = {}

            self.sub_orgs_data = await self._fetch_sub_organizations()
            self.users_data = await self._fetch_users()
            self.groups_data = await self._fetch_groups()
            self.roles_data = await self._fetch_roles()

            last_sync_data = await self.tickets_sync_point.read_sync_point("tickets")
            last_sync_time = last_sync_data.get("last_sync_time") if last_sync_data else None

            if last_sync_time:
                await self._fetch_tickets_incremental(last_sync_time)
            else:
                self.tickets_data = await self._fetch_tickets()

            kb_categories_sync_data = await self.kb_categories_sync_point.read_sync_point("kb_categories")
            kb_answers_sync_data = await self.kb_answers_sync_point.read_sync_point("kb_answers")

            categories_last_sync = kb_categories_sync_data.get("last_sync_time") if kb_categories_sync_data else None
            answers_last_sync = kb_answers_sync_data.get("last_sync_time") if kb_answers_sync_data else None

            if categories_last_sync or answers_last_sync:
                await self._fetch_kb_incremental(categories_last_sync, answers_last_sync)
            else:
                self.knowledge_base_data = await self._fetch_knowledge_base()

        except Exception as e:
            self.logger.error(f"Error fetching entities incrementally: {e}", exc_info=True)
            raise

    async def _build_nodes_and_edges_incremental(self) -> None:
        """Build nodes and edges for incremental sync - uses incremental fetching but full edge processing"""
        try:
            org_id: Optional[str] = self.data_entities_processor.org_id
            if not org_id:
                raise ValueError("Organization ID not found")

            apps: List[Dict[str, Any]] = await self.data_store_provider.arango_service.get_org_apps(org_id)
            app_key: Optional[str] = next((a.get("_key") for a in apps if a.get("type") == Connectors.ZAMMAD.value), None)

            if not app_key:
                self.logger.warning("Zammad app not found for organization")
                return

            if not self.roles_data:
                self.roles_data = await self._fetch_roles()

            if not self.groups_data:
                self.groups_data = await self._fetch_groups()

            async with self.data_store_provider.transaction() as tx_store:
                await self._create_role_nodes(tx_store)

            sub_org_map: Dict[str, str] = await self._process_sub_organizations(org_id, app_key, all_users_data=self.users_data)
            user_id_map: Dict[str, str] = await self._process_users(org_id, app_key, sub_org_map)

            groups_map: Dict[str, str] = await self._process_groups(org_id, app_key)
            await self._create_group_app_edges(app_key, groups_map, sub_org_map)

            async with self.data_store_provider.transaction() as tx_store:
                if self.tickets_data and len(self.tickets_data) > 0:
                    await self._process_tickets(tx_store, org_id, sub_org_map, user_id_map, self.users_data)

                if self.kb_categories_data or self.kb_answers_data:
                    await self._process_knowledge_base(org_id, app_key)


        except Exception as e:
            self.logger.error(f"Error building nodes and edges for incremental sync: {e}", exc_info=True)
            raise

    async def _update_sync_points(self) -> None:
        """Update sync points after successful incremental sync"""
        try:
            if self.users_data and len(self.users_data) > 0:
                latest_user = max(self.users_data, key=lambda u: u.get("updated_at") or u.get("created_at") or datetime.min.replace(tzinfo=timezone.utc))
                latest_user_timestamp = latest_user.get("updated_at") if latest_user.get("updated_at") else latest_user.get("created_at")
                if latest_user_timestamp:
                    latest_user_timestamp_str = latest_user_timestamp if isinstance(latest_user_timestamp, str) else latest_user_timestamp.isoformat()
                    try:
                        await self.users_sync_point.update_sync_point(
                            "users",
                            {"last_sync_time": latest_user_timestamp_str}
                        )
                    except Exception as sync_error:
                        self.logger.error(f"Failed to save users sync point: {sync_error}", exc_info=True)
            else:
                last_sync_data = await self.users_sync_point.read_sync_point("users")
                if last_sync_data:
                    current_time = datetime.now(timezone.utc).isoformat()
                    try:
                        await self.users_sync_point.update_sync_point(
                            "users",
                            {"last_sync_time": current_time}
                        )
                    except Exception as sync_error:
                        self.logger.error(f"Failed to update users sync point: {sync_error}", exc_info=True)

            if self.tickets_data and len(self.tickets_data) > 0:
                latest_ticket = max(self.tickets_data, key=lambda t: t.get("updated_at") or t.get("created_at") or datetime.min.replace(tzinfo=timezone.utc))
                latest_timestamp = latest_ticket.get("updated_at") if latest_ticket.get("updated_at") else latest_ticket.get("created_at")
                if latest_timestamp:
                    latest_timestamp_str = latest_timestamp if isinstance(latest_timestamp, str) else latest_timestamp.isoformat()
                    try:
                        await self.tickets_sync_point.update_sync_point(
                            "tickets",
                            {"last_sync_time": latest_timestamp_str}
                        )
                    except Exception as sync_error:
                        self.logger.error(f"Failed to save tickets sync point: {sync_error}", exc_info=True)
            else:
                last_sync_data = await self.tickets_sync_point.read_sync_point("tickets")
                if last_sync_data:
                    current_time = datetime.now(timezone.utc).isoformat()
                    try:
                        await self.tickets_sync_point.update_sync_point(
                            "tickets",
                            {"last_sync_time": current_time}
                        )
                    except Exception as sync_error:
                        self.logger.error(f"Failed to update tickets sync point: {sync_error}", exc_info=True)

            if self.kb_categories_data and len(self.kb_categories_data) > 0:
                latest_category = max(self.kb_categories_data, key=lambda c: c.get("updated_at") or c.get("created_at") or datetime.min.replace(tzinfo=timezone.utc))
                latest_cat_timestamp = latest_category.get("updated_at") if latest_category.get("updated_at") else latest_category.get("created_at")
                if latest_cat_timestamp:
                    latest_cat_timestamp_str = latest_cat_timestamp if isinstance(latest_cat_timestamp, str) else latest_cat_timestamp.isoformat()
                    try:
                        await self.kb_categories_sync_point.update_sync_point(
                            "kb_categories",
                            {"last_sync_time": latest_cat_timestamp_str}
                        )
                    except Exception as sync_error:
                        self.logger.error(f"Failed to save KB categories sync point: {sync_error}", exc_info=True)
            else:
                kb_categories_sync_data = await self.kb_categories_sync_point.read_sync_point("kb_categories")
                if kb_categories_sync_data:
                    current_time = datetime.now(timezone.utc).isoformat()
                    try:
                        await self.kb_categories_sync_point.update_sync_point(
                            "kb_categories",
                            {"last_sync_time": current_time}
                        )
                    except Exception as sync_error:
                        self.logger.error(f"Failed to update KB categories sync point: {sync_error}", exc_info=True)

            if self.kb_answers_data and len(self.kb_answers_data) > 0:
                latest_answer = max(self.kb_answers_data, key=lambda a: a.get("updated_at") or a.get("created_at") or datetime.min.replace(tzinfo=timezone.utc))
                latest_ans_timestamp = latest_answer.get("updated_at") if latest_answer.get("updated_at") else latest_answer.get("created_at")
                if latest_ans_timestamp:
                    latest_ans_timestamp_str = latest_ans_timestamp if isinstance(latest_ans_timestamp, str) else latest_ans_timestamp.isoformat()
                    try:
                        await self.kb_answers_sync_point.update_sync_point(
                            "kb_answers",
                            {"last_sync_time": latest_ans_timestamp_str}
                        )
                    except Exception as sync_error:
                        self.logger.error(f"Failed to save KB answers sync point: {sync_error}", exc_info=True)
            else:
                kb_answers_sync_data = await self.kb_answers_sync_point.read_sync_point("kb_answers")
                if kb_answers_sync_data:
                    current_time = datetime.now(timezone.utc).isoformat()
                    try:
                        await self.kb_answers_sync_point.update_sync_point(
                            "kb_answers",
                            {"last_sync_time": current_time}
                        )
                    except Exception as sync_error:
                        self.logger.error(f"Failed to update KB answers sync point: {sync_error}", exc_info=True)

        except Exception as e:
            self.logger.error(f"Error updating sync points: {e}", exc_info=True)
            raise

    async def _create_initial_sync_points(self) -> None:
        """Create initial sync points after first full sync for all entities"""
        try:
            if self.users_data and len(self.users_data) > 0:
                latest_user = max(self.users_data, key=lambda u: u.get("updated_at") or u.get("created_at") or datetime.min.replace(tzinfo=timezone.utc))
                latest_user_timestamp = latest_user.get("updated_at") if latest_user.get("updated_at") else latest_user.get("created_at")
                if latest_user_timestamp:
                    latest_user_timestamp_str = (
                        latest_user_timestamp.isoformat() if isinstance(latest_user_timestamp, datetime) else str(latest_user_timestamp)
                    )
                    try:
                        await self.users_sync_point.update_sync_point(
                            "users",
                            {"last_sync_time": latest_user_timestamp_str}
                        )
                    except Exception as sync_error:
                        self.logger.error(f"Failed to create users sync point: {sync_error}", exc_info=True)

            if self.tickets_data and len(self.tickets_data) > 0:
                latest_ticket = max(self.tickets_data, key=lambda t: t.get("updated_at") or t.get("created_at") or datetime.min.replace(tzinfo=timezone.utc))
                latest_timestamp = latest_ticket.get("updated_at") if latest_ticket.get("updated_at") else latest_ticket.get("created_at")
                if latest_timestamp:
                    latest_timestamp_str = (
                        latest_timestamp.isoformat() if isinstance(latest_timestamp, datetime) else str(latest_timestamp)
                    )
                    try:
                        await self.tickets_sync_point.update_sync_point(
                            "tickets",
                            {"last_sync_time": latest_timestamp_str}
                        )
                    except Exception as sync_error:
                        self.logger.error(f"Failed to create tickets sync point: {sync_error}", exc_info=True)

            if self.kb_categories_data and len(self.kb_categories_data) > 0:
                latest_category = max(self.kb_categories_data, key=lambda c: c.get("updated_at") or c.get("created_at") or datetime.min.replace(tzinfo=timezone.utc))
                latest_cat_timestamp = latest_category.get("updated_at") if latest_category.get("updated_at") else latest_category.get("created_at")
                if latest_cat_timestamp:
                    latest_cat_timestamp_str = (
                        latest_cat_timestamp.isoformat() if isinstance(latest_cat_timestamp, datetime) else str(latest_cat_timestamp)
                    )
                    try:
                        await self.kb_categories_sync_point.update_sync_point(
                            "kb_categories",
                            {"last_sync_time": latest_cat_timestamp_str}
                        )
                    except Exception as sync_error:
                        self.logger.error(f"Failed to create KB categories sync point: {sync_error}", exc_info=True)

            if self.kb_answers_data and len(self.kb_answers_data) > 0:
                latest_answer = max(self.kb_answers_data, key=lambda a: a.get("updated_at") or a.get("created_at") or datetime.min.replace(tzinfo=timezone.utc))
                latest_ans_timestamp = latest_answer.get("updated_at") if latest_answer.get("updated_at") else latest_answer.get("created_at")
                if latest_ans_timestamp:
                    latest_ans_timestamp_str = (
                        latest_ans_timestamp.isoformat() if isinstance(latest_ans_timestamp, datetime) else str(latest_ans_timestamp)
                    )
                    try:
                        await self.kb_answers_sync_point.update_sync_point(
                            "kb_answers",
                            {"last_sync_time": latest_ans_timestamp_str}
                        )
                    except Exception as sync_error:
                        self.logger.error(f"Failed to create KB answers sync point: {sync_error}", exc_info=True)

        except Exception as e:
            self.logger.error(f"Error creating initial sync points: {e}", exc_info=True)

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

    async def get_signed_url(self, record: Record) -> str:
        """Return the weburl (Zammad doesn't need signed URLs)"""
        return record.weburl or ""

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

    async def _fetch_entities(self) -> None:
        """Fetch entities from Zammad"""
        try:
            # Clear KB data from any previous runs
            self.kb_categories_data = []
            self.kb_answers_data = []
            self.kb_full_response_data = {}

            self.sub_orgs_data = await self._fetch_sub_organizations()
            if not self.sub_orgs_data or len(self.sub_orgs_data) == 0:
                return

            self.users_data = await self._fetch_users()
            if not self.users_data or len(self.users_data) == 0:
                return

            self.groups_data = await self._fetch_groups()
            if not self.groups_data or len(self.groups_data) == 0:
                return

            self.tickets_data = await self._fetch_tickets()
            if not self.tickets_data or len(self.tickets_data) == 0:
                return

            self.knowledge_base_data = await self._fetch_knowledge_base()
            if not self.knowledge_base_data or len(self.knowledge_base_data) == 0:
                self.logger.warning("No Knowledge Base found or failed to fetch Knowledge Base")

            self.roles_data = await self._fetch_roles()

        except Exception as e:
            self.logger.error(f"Error fetching entities: {e}", exc_info=True)
            raise

    async def _fetch_sub_organizations(self) -> List[Dict[str, Any]]:
        """Fetch Zammad sub-organizations"""
        try:
            response: ZammadResponse = await self.zammad_datasource.list_organizations(expand="true")
            if not response.success or not response.data:
                return []

            organizations: List[Dict[str, Any]] = []
            data_list: List[Dict[str, Any]] = response.data if isinstance(response.data, list) else []
            for org_data in data_list:
                try:
                    org: Dict[str, Any] = org_data
                    organizations.append(org)
                except Exception as e:
                    self.logger.warning(f"Failed to parse organization {org_data.get('id')}: {e}")

            return organizations
        except Exception as e:
            self.logger.error(f"Error fetching sub-organizations: {e}", exc_info=True)
            return []

    async def _fetch_users(self) -> List[Dict[str, Any]]:
        """Fetch Zammad users"""
        try:
            response: ZammadResponse = await self.zammad_datasource.list_users(expand="true")
            if not response.success or not response.data:
                return []

            users: List[Dict[str, Any]] = []
            data_list: List[Dict[str, Any]] = response.data if isinstance(response.data, list) else []
            for user_data in data_list:
                try:
                    user: Dict[str, Any] = user_data
                    users.append(user)
                except Exception as e:
                    self.logger.warning(f"Failed to parse user {user_data.get('id')}: {e}")

            return users
        except Exception as e:
            self.logger.error(f"Error fetching users: {e}", exc_info=True)
            return []

    async def _fetch_groups(self) -> List[Dict[str, Any]]:
        """Fetch Zammad groups"""
        try:
            response: ZammadResponse = await self.zammad_datasource.list_groups(expand="true")
            if not response.success or not response.data:
                return []

            groups: List[Dict[str, Any]] = []
            data_list: List[Dict[str, Any]] = response.data if isinstance(response.data, list) else []
            for group_data in data_list:
                try:
                    group: Dict[str, Any] = group_data
                    groups.append(group)
                except Exception as e:
                    self.logger.warning(f"Failed to parse group {group_data.get('id')}: {e}")

            return groups
        except Exception as e:
            self.logger.error(f"Error fetching groups: {e}", exc_info=True)
            return []

    async def _fetch_tickets(self) -> List[Dict[str, Any]]:
        """Fetch Zammad tickets"""
        try:
            response: ZammadResponse = await self.zammad_datasource.list_tickets(expand="true")
            if not response.success or not response.data:
                return []

            tickets: List[Dict[str, Any]] = []
            data_list: List[Dict[str, Any]] = response.data if isinstance(response.data, list) else []
            for ticket_data in data_list:
                try:
                    ticket: Dict[str, Any] = ticket_data
                    tickets.append(ticket)
                except Exception as e:
                    self.logger.warning(f"Failed to parse ticket {ticket_data.get('id')}: {e}")

            return tickets
        except Exception as e:
            self.logger.error(f"Error fetching tickets: {e}", exc_info=True)
            return []

    async def _fetch_users_incremental(self, last_sync_time: str) -> None:
        """
        Fetch users modified after last_sync_time.
        """
        try:

            # Parse the last_sync_time for comparison
            last_sync_dt = datetime.fromisoformat(last_sync_time.replace('Z', '+00:00'))

            # Fetch all users (Zammad doesn't support incremental user API)
            response: ZammadResponse = await self.zammad_datasource.list_users()

            if not response.success or not response.data:
                self.logger.info("No users found")
                self.users_data = []
                return

            all_users: List[Dict[str, Any]] = []
            data_list: List[Dict[str, Any]] = response.data if isinstance(response.data, list) else []

            for user_data in data_list:
                try:
                    user: Dict[str, Any] = user_data
                    all_users.append(user)
                except Exception as e:
                    self.logger.warning(f"Failed to parse user {user_data.get('id')}: {e}")

            # Filter users client-side by updated_at timestamp
            filtered_users: List[Dict[str, Any]] = []
            for user in all_users:
                if user.get("updated_at"):
                    # Parse the string datetime
                    user_updated_dt = self._parse_datetime_string(user.get("updated_at"))
                    if user_updated_dt and user_updated_dt > last_sync_dt:
                        filtered_users.append(user)

            self.users_data = filtered_users

        except Exception as e:
            self.logger.error(f"Error fetching users incrementally: {e}", exc_info=True)
            self.users_data = []

    async def _fetch_tickets_incremental(self, last_sync_time: str) -> None:
        """
        Fetch tickets modified after last_sync_time.
        """
        try:

            # Parse the last_sync_time for comparison
            last_sync_dt = datetime.fromisoformat(last_sync_time.replace('Z', '+00:00'))

            # Fetch all tickets (Zammad search doesn't filter properly by timestamp)
            response: ZammadResponse = await self.zammad_datasource.list_tickets(expand="true")

            if not response.success or not response.data:
                self.tickets_data = []
                return

            all_tickets: List[Dict[str, Any]] = []
            data_list: List[Dict[str, Any]] = response.data if isinstance(response.data, list) else []

            for ticket_data in data_list:
                try:
                    ticket: Dict[str, Any] = ticket_data
                    all_tickets.append(ticket)
                except Exception as e:
                    self.logger.warning(f"Failed to parse ticket {ticket_data.get('id')}: {e}")

            # Filter tickets client-side by updated_at timestamp
            filtered_tickets: List[Dict[str, Any]] = []
            for ticket in all_tickets:
                if ticket.get("updated_at"):
                    # Parse the string datetime
                    ticket_updated_dt = self._parse_datetime_string(ticket.get("updated_at"))
                    if ticket_updated_dt and ticket_updated_dt > last_sync_dt:
                        filtered_tickets.append(ticket)

            self.tickets_data = filtered_tickets

        except Exception as e:
            self.logger.error(f"Error fetching tickets incrementally: {e}", exc_info=True)
            self.tickets_data = []

    async def _fetch_kb_incremental(self, categories_last_sync: Optional[str] = None, answers_last_sync: Optional[str] = None) -> None:
        """
        Args:
            categories_last_sync: ISO format timestamp for categories (e.g., "2024-01-15T10:30:00Z")
            answers_last_sync: ISO format timestamp for answers (e.g., "2024-01-15T10:30:00Z")
        """
        try:

            categories_sync_dt = datetime.fromisoformat(categories_last_sync.replace('Z', '+00:00')) if categories_last_sync else None
            answers_sync_dt = datetime.fromisoformat(answers_last_sync.replace('Z', '+00:00')) if answers_last_sync else None

            await self._fetch_knowledge_base()

            if categories_sync_dt and self.kb_categories_data:
                filtered_categories: List[Dict[str, Any]] = []

                for category in self.kb_categories_data:
                    cat_id = category.get("id")
                    cat_title = category.get("title", "Untitled")
                    cat_updated = category.get("updated_at")

                    if cat_updated:
                        cat_updated_dt = self._parse_datetime_string(cat_updated)
                        if cat_updated_dt and cat_updated_dt > categories_sync_dt:
                            filtered_categories.append(category)
                        else:
                            self.logger.debug(f"Category {cat_id} ({cat_title}): NOT updated (last: {cat_updated})")
                    else:
                        self.logger.warning(f"Category {cat_id} has no updated_at timestamp")

                self.kb_categories_data = filtered_categories

            # Filter answers client-side if we have a sync point
            if answers_sync_dt and self.kb_answers_data:
                filtered_answers: List[Dict[str, Any]] = []

                for answer in self.kb_answers_data:
                    ans_id = answer.get("id")
                    ans_updated = answer.get("updated_at")

                    if ans_updated:
                        ans_updated_dt = self._parse_datetime_string(ans_updated)
                        if ans_updated_dt and ans_updated_dt > answers_sync_dt:
                            filtered_answers.append(answer)
                        else:
                            self.logger.debug(f"Answer {ans_id}: NOT updated (last: {ans_updated})")
                    else:
                        self.logger.warning(f"Answer {ans_id} has no updated_at timestamp")

                self.kb_answers_data = filtered_answers

        except Exception as e:
            self.logger.error(f"Error fetching KB incrementally: {e}", exc_info=True)
            self.kb_categories_data = []
            self.kb_answers_data = []

    async def _fetch_knowledge_base(self) -> None:
        """Fetch and parse Zammad Knowledge Base using init_knowledge_base response"""
        try:
            response: ZammadResponse = await self.zammad_datasource.init_knowledge_base()
            if not response.success or not response.data:
                self.knowledge_base_data = []
                self.kb_categories_data = []
                self.kb_answers_data = []
                self.kb_full_response_data = {}
                return

            data = response.data
            # Store full response data for attachment access later
            self.kb_full_response_data = data
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
                kb_obj = {
                    "id": kb.get("id"),
                    "title": title,
                    "created_at": self._parse_datetime_string(kb.get("created_at")) if kb.get("created_at") else None,
                    "updated_at": self._parse_datetime_string(kb.get("updated_at")) if kb.get("updated_at") else None
                }
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
                cat_obj = {
                    "id": cat.get("id"),
                    "knowledge_base_id": cat.get("knowledge_base_id"),
                    "parent_id": cat.get("parent_id"),
                    "title": title,
                    "answer_ids": cat.get("answer_ids", []),
                    "child_ids": cat.get("child_ids", []),
                    "permission_ids": cat.get("permission_ids", []),
                    "permissions_effective": cat.get("permissions_effective", []),
                    "created_at": self._parse_datetime_string(cat.get("created_at")) if cat.get("created_at") else None,
                    "updated_at": self._parse_datetime_string(cat.get("updated_at")) if cat.get("updated_at") else None
                }
                self.kb_categories_data.append(cat_obj)

            self.kb_answers_data = []
            for ans_id, ans in answer_map.items():
                title = ""
                translation_ids = ans.get("translation_ids", [])
                if translation_ids:
                    translation_id = str(translation_ids[0])
                    translation = answer_translations.get(translation_id, {})
                    title = translation.get("title", "")

                attachments_data = ans.get("attachments", [])
                attachment_ids = [att.get("id") for att in attachments_data if att.get("id")]

                ans_obj = {
                    "id": ans.get("id"),
                    "category_id": ans.get("category_id"),
                    "title": title,
                    "attachment_ids": attachment_ids,
                    "created_at": self._parse_datetime_string(ans.get("created_at")) if ans.get("created_at") else None,
                    "updated_at": self._parse_datetime_string(ans.get("updated_at")) if ans.get("updated_at") else None
                }
                self.kb_answers_data.append(ans_obj)

        except Exception as e:
            self.logger.error(f"Error parsing Knowledge Base from Zammad: {e}", exc_info=True)
            self.knowledge_base_data = []
            self.kb_categories_data = []
            self.kb_answers_data = []
            self.kb_full_response_data = {}

    async def _fetch_roles(self) -> List[Dict[str, Any]]:
        """Fetch Zammad Roles"""
        try:
            response: ZammadResponse = await self.zammad_datasource.list_roles(expand="true")
            if not response.success or not response.data:
                return []

            roles: List[Dict[str, Any]] = []
            data_list: List[Dict[str, Any]] = response.data if isinstance(response.data, list) else []
            for role_data in data_list:
                try:
                    roles.append(role_data)
                except Exception as e:
                    self.logger.warning(f"Failed to parse role {role_data.get('id')}: {e}")

            return roles
        except Exception as e:
            self.logger.error(f"Error fetching Roles: {e}", exc_info=True)
            return []

    async def _process_knowledge_base(self, org_id: str, app_key: str) -> None:
        """Process KB categories as RecordGroups and KB answers as WebpageRecords"""
        try:

            # Step 1: Process KB categories as RecordGroups (no transaction needed - edges created separately)
            kb_category_map: Dict[str, str] = await self._process_kb_categories(org_id, app_key)
            await self._process_kb_answers_as_records(org_id, kb_category_map)

        except Exception as e:
            self.logger.error(f"Error processing Knowledge Base: {e}", exc_info=True)
            raise

    async def _process_kb_categories(self, org_id: str, app_key: str) -> Dict[str, str]:

        """Process KB categories as RecordGroups. Returns mapping of category_id to record_group_id"""
        try:

            category_map: Dict[str, str] = {}
            record_groups_with_permissions: List[Tuple[RecordGroup, List[Permission]]] = []

            for category in self.kb_categories_data:
                external_category_id: str = str(category.get("id"))
                created_at: int = self._parse_datetime_to_timestamp(category.get("created_at"))
                updated_at: int = self._parse_datetime_to_timestamp(category.get("updated_at"))
                category_name: str = category.get('title') if category.get('title') else f"KB Category {external_category_id}"

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

                category_permissions: List[Permission] = self._extract_category_permissions(category)
                record_groups_with_permissions.append((record_group, category_permissions))

            if record_groups_with_permissions:
                await self.data_entities_processor.on_new_record_groups(record_groups_with_permissions)
                self.logger.info(f"Processed {len(record_groups_with_permissions)} KB category RecordGroups")

                for record_group, _ in record_groups_with_permissions:
                    category_map[record_group.external_group_id] = record_group.id

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
                        try:
                            await tx_store.batch_create_edges(
                                record_group_app_edges,
                                CollectionNames.BELONGS_TO.value
                            )
                        except Exception as e:
                            self.logger.warning(f"Some KB category-to-App edges may already exist: {e}")

            return category_map

        except Exception as e:
            self.logger.error(f"Error processing KB categories: {e}", exc_info=True)
            raise

    def _create_attachment_records(
        self,
        answer_id: int,
        org_id: str,
        created_at: int,
        updated_at: int
    ) -> List[Tuple[FileRecord, Record]]:
        """
        Create FileRecord and Record for each attachment of a KB answer.
        """
        attachment_records: List[Tuple[FileRecord, Record]] = []

        # Get answer data from full response
        answer_map = self.kb_full_response_data.get("KnowledgeBaseAnswer", {})
        answer_data = answer_map.get(str(answer_id))

        if not answer_data:
            return attachment_records

        attachments = answer_data.get("attachments", [])
        if not attachments:
            return attachment_records

        for attachment in attachments:
            try:
                attachment_id = attachment.get("id")
                if not attachment_id:
                    continue

                filename = attachment.get("filename", f"attachment_{attachment_id}")
                size = int(attachment.get("size", 0))
                mime_type = attachment.get("preferences", {}).get("Content-Type", "application/octet-stream")

                # Extract extension from filename
                extension = None
                if "." in filename:
                    extension = filename.split(".")[-1].lower()

                # Construct download URL - must be a string for schema validation
                download_url = f"{self.base_url}{attachment.get('url')}" if self.base_url and attachment.get('url') else ""

                # Generate unique key for this attachment
                file_record_key = str(uuid4())

                file_record = FileRecord(
                    id=file_record_key,
                    org_id=org_id,
                    record_name=filename,
                    record_type=RecordType.FILE,
                    external_record_id=str(attachment_id),
                    external_record_group_id="",
                    connector_name=Connectors.ZAMMAD,
                    origin=OriginTypes.CONNECTOR,
                    version=0,
                    created_at=created_at,
                    updated_at=updated_at,
                    source_created_at=created_at,
                    source_updated_at=updated_at,
                    is_file=True,
                    extension=extension,
                    mime_type=mime_type,
                    size_in_bytes=size,
                    weburl=download_url
                )

                record = Record(
                    id=file_record_key,
                    org_id=org_id,
                    record_name=filename,
                    record_type=RecordType.FILE,
                    external_record_id=str(attachment_id),
                    connector_name=Connectors.ZAMMAD,
                    origin=OriginTypes.CONNECTOR,
                    version=0,
                    created_at=created_at,
                    updated_at=updated_at,
                    source_created_at=created_at,
                    source_updated_at=updated_at,
                    indexing_status="NOT_STARTED",
                    extraction_status="NOT_STARTED",
                    mime_type=mime_type,
                    weburl=download_url
                )

                attachment_records.append((file_record, record))


            except Exception as e:
                self.logger.error(f"Error creating attachment record for attachment {attachment.get('id')}: {e}", exc_info=True)
                continue

        return attachment_records

    def _create_ticket_attachment_records(
        self,
        ticket_id: int,
        articles: List[Dict[str, Any]],
        org_id: str,
        created_at: int,
        updated_at: int
    ) -> List[Tuple[FileRecord, Record]]:
        """
        Create FileRecord and Record for each attachment in ticket articles.
        """
        attachment_records: List[Tuple[FileRecord, Record]] = []

        for article in articles:
            attachments = article.get("attachments", [])
            if not attachments:
                continue

            for attachment in attachments:
                try:
                    attachment_id = attachment.get("id")
                    if not attachment_id:
                        continue

                    filename = attachment.get("filename", f"attachment_{attachment_id}")
                    size = int(attachment.get("size", 0))
                    mime_type = attachment.get("preferences", {}).get("Content-Type", "application/octet-stream")

                    extension = None
                    if "." in filename:
                        extension = filename.split(".")[-1].lower()

                    download_url = f"{self.base_url}/#ticket/zoom/{ticket_id}" if self.base_url else ""

                    file_record_key = str(uuid4())

                    file_record = FileRecord(
                        id=file_record_key,
                        org_id=org_id,
                        record_name=filename,
                        record_type=RecordType.FILE,
                        external_record_id=str(attachment_id),
                        external_record_group_id="",
                        connector_name=Connectors.ZAMMAD,
                        origin=OriginTypes.CONNECTOR,
                        version=0,
                        created_at=created_at,
                        updated_at=updated_at,
                        source_created_at=created_at,
                        source_updated_at=updated_at,
                        is_file=True,
                        extension=extension,
                        mime_type=mime_type,
                        size_in_bytes=size,
                        weburl=download_url
                    )

                    # Create Record (same key as FileRecord)
                    record = Record(
                        id=file_record_key,  # MUST match FileRecord key
                        org_id=org_id,
                        record_name=filename,
                        record_type=RecordType.FILE,
                        external_record_id=str(attachment_id),
                        connector_name=Connectors.ZAMMAD,
                        origin=OriginTypes.CONNECTOR,
                        version=0,
                        created_at=created_at,
                        updated_at=updated_at,
                        source_created_at=created_at,
                        source_updated_at=updated_at,
                        indexing_status="NOT_STARTED",
                        extraction_status="NOT_STARTED",
                        mime_type=mime_type,
                        weburl=download_url
                    )

                    attachment_records.append((file_record, record))


                except Exception as e:
                    self.logger.error(f"Error creating ticket attachment record for attachment {attachment.get('id')}: {e}", exc_info=True)
                    continue

        return attachment_records

    async def _process_kb_answers_as_records(self, org_id: str, kb_category_map: Dict[str, str]) -> None:
        """Process KB answers as WebpageRecords with proper indexing"""
        try:
            # Build category_permissions map from KB categories
            category_permissions: Dict[str, List[Dict[str, Any]]] = {}
            for category in self.kb_categories_data:
                category_id = str(category.get("id"))
                category_permissions[category_id] = category.get("permissions_effective", [])

            records_with_permissions: List[Tuple[WebpageRecord, List[Permission]]] = []

            attachment_records_with_permissions: List[Tuple[FileRecord, List[Permission]]] = []
            all_attachment_relation_edges: List[Dict[str, Any]] = []

            # Use a single transaction for all KB answer processing (like tickets)
            async with self.data_store_provider.transaction() as tx_store:
                for answer in self.kb_answers_data:
                    external_answer_id: str = str(answer.get("id"))
                    created_at: int = self._parse_datetime_to_timestamp(answer.get("created_at"))
                    updated_at: int = self._parse_datetime_to_timestamp(answer.get("updated_at"))

                    answer_title: str = answer.get('title') or f"KB Answer {external_answer_id}"
                    if not answer_title and answer.get("translations", []) and len(answer.get("translations", [])) > 0:
                        first_translation = answer.get("translations", [])[0]
                        answer_title = first_translation.get('title', f"KB Answer {external_answer_id}")

                    category_id: str = str(answer.get("category_id"))
                    external_group_id: Optional[str] = category_id if category_id in kb_category_map else None

                    if not external_group_id:
                        self.logger.warning(f"Category {category_id} not found in kb_category_map for answer {external_answer_id}")

                    # Check if record already exists (for deduplication)
                    existing_record = await tx_store.get_record_by_external_id(
                        connector_name=Connectors.ZAMMAD,
                        external_id=external_answer_id,
                        record_type="WEBPAGE"
                    )

                    record_id: str = existing_record.id if existing_record else str(uuid4())
                    is_new: bool = existing_record is None

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
                        weburl=f"{self.base_url}/help/en-us/{category_id}/{answer.get('id')}" if self.base_url else None,
                        external_record_group_id=external_group_id,
                        record_group_type=RecordGroupType.KB,
                        created_at=created_at,
                        updated_at=updated_at,
                        source_created_at=created_at,
                        source_updated_at=updated_at
                    )

                    # Build permissions list using helper - KB articles with role-based permissions
                    record_permissions: List[Permission] = self._extract_kb_permissions(
                        answer=answer,
                        category_permissions=category_permissions
                    )

                    records_with_permissions.append((webpage_record, record_permissions))

                    # Process attachments for this KB answer
                    attachment_ids = answer.get("attachment_ids", [])
                    if attachment_ids:
                        attachment_records = self._create_attachment_records(
                            answer_id=answer.get("id"),
                            org_id=org_id,
                            created_at=created_at,
                            updated_at=updated_at
                        )

                        # For UPDATED KB answers, filter out attachments that already exist in database
                        if not is_new:
                            filtered_attachment_records = []
                            for file_record, _ in attachment_records:
                                # Check if attachment already exists
                                existing_attachment = await tx_store.get_record_by_external_id(
                                    connector_name=Connectors.ZAMMAD,
                                    external_id=file_record.external_record_id,
                                    record_type="FILE"
                                )
                                if existing_attachment is None:
                                    filtered_attachment_records.append((file_record, _))

                            attachment_records = filtered_attachment_records

                        for file_record, _ in attachment_records:

                            attachment_permissions: List[Permission] = record_permissions.copy()
                            attachment_records_with_permissions.append((file_record, attachment_permissions))

                            attachment_relation_edge: Dict[str, Any] = {
                                "_from": f"{CollectionNames.RECORDS.value}/{record_id}",
                                "_to": f"{CollectionNames.RECORDS.value}/{file_record.id}",
                                "relationType": RecordRelations.ATTACHMENT.value,
                                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                                "updatedAtTimestamp": get_epoch_timestamp_in_ms()
                            }
                            all_attachment_relation_edges.append(attachment_relation_edge)


            new_records_with_permissions = [
                (record, perms) for record, perms in records_with_permissions
                if record.version == 0
            ]

            updated_records = [
                record for record, perms in records_with_permissions
                if record.version > 0
            ]

            if new_records_with_permissions:
                await self.data_entities_processor.on_new_records(new_records_with_permissions)

            if updated_records:
                async with self.data_store_provider.transaction() as tx_store_update:
                    webpage_records_to_update = [record for record in updated_records if isinstance(record, WebpageRecord)]
                    if webpage_records_to_update:
                        await tx_store_update.batch_upsert_records(webpage_records_to_update)

                for record in updated_records:
                    await self.data_entities_processor.on_record_content_update(record)

            if attachment_records_with_permissions:
                await self.data_entities_processor.on_new_records(attachment_records_with_permissions)

            async with self.data_store_provider.transaction() as tx_store_2:
                if all_attachment_relation_edges:
                    await tx_store_2.batch_create_edges(
                        all_attachment_relation_edges,
                        CollectionNames.RECORD_RELATIONS.value
                    )

        except Exception as e:
            self.logger.error(f"Error processing KB answers as records: {e}", exc_info=True)
            raise

    async def _get_ticket_content(self, ticket: Dict[str, Any], articles: List[Dict[str, Any]]) -> str:

        """ Combine ticket title, description, and article bodies into HTML format"""

        content_parts: List[str] = []

        if ticket.get("title"):
            content_parts.append(f"<h1>{ticket.get('title')}</h1>")

        state_map = {
            1: "closed",
            2: "new",
            3: "open",
            4: "pending close",
            5: "pending reminder",
        }
        status_value: str = state_map.get(ticket.get('state_id'), f"state_{ticket.get('state_id')}") if ticket.get('state_id') else "Unknown"

        priority_map = {
            1: "low",
            2: "normal",
            3: "high"
        }
        priority_value: str = priority_map.get(ticket.get('priority_id'), f"priority_{ticket.get('priority_id')}") if ticket.get('priority_id') else "Unknown"

        content_parts.append("<div class='ticket-metadata'>")
        content_parts.append(f"<p><strong>Status:</strong> {status_value}</p>")
        content_parts.append(f"<p><strong>Priority:</strong> {priority_value}</p>")
        content_parts.append("</div>")

        if articles:
            content_parts.append("<h2>Conversation:</h2>")
            content_parts.append("<div class='ticket-articles'>")
            for article in articles:
                content_parts.append("<div class='article'>")

                from_name = article.get('from', 'Unknown')
                content_parts.append(f"<p><strong>From:</strong> {from_name}</p>")

                body = article.get('body', '')
                if body:
                    content_parts.append(f"<div class='article-body'>{body}</div>")

                # Add attachments if present - embed actual content
                attachments = article.get('attachments', [])
                if attachments:
                    content_parts.append("<div class='article-attachments'>")
                    content_parts.append("<p><strong>Attachments:</strong></p>")

                    for attachment in attachments:
                        attachment_id = attachment.get('id', '')
                        filename = attachment.get('filename', 'Unknown file')
                        mime_type = attachment.get('preferences', {}).get('Content-Type', 'application/octet-stream')

                        content_parts.append("<div class='attachment' style='margin: 10px 0; padding: 10px; border: 1px solid #ddd;'>")

                        try:
                            # Download attachment content
                            attachment_response: ZammadResponse = await self.zammad_datasource.download_attachment(int(attachment_id))

                            if attachment_response.success and attachment_response.data:
                                # Handle different file types
                                if mime_type.startswith('image/'):
                                    # Embed images as base64
                                    import base64
                                    b64_data = base64.b64encode(attachment_response.data).decode('ascii')
                                    content_parts.append(f"<img src='data:{mime_type};base64,{b64_data}' style='max-width: 100%; height: auto;' alt='{filename}' />")

                                elif mime_type == 'application/pdf':
                                    # Embed PDF using iframe with base64
                                    import base64
                                    b64_data = base64.b64encode(attachment_response.data).decode('ascii')
                                    content_parts.append(f"<iframe src='data:application/pdf;base64,{b64_data}' style='width: 100%; height: 600px; border: 1px solid #ccc;' title='{filename}'></iframe>")

                                else:
                                    # For other files, provide download info
                                    content_parts.append(f"<p>File type: {mime_type}</p>")
                                    content_parts.append(f"<p><em>Download available through API (ID: {attachment_id})</em></p>")
                            else:
                                content_parts.append("<p><em>Failed to load attachment content</em></p>")

                        except Exception as e:
                            self.logger.error(f"Error embedding attachment {attachment_id}: {e}")
                            content_parts.append(f"<p><em>Error loading attachment: {str(e)}</em></p>")

                        content_parts.append("</div>")

                    content_parts.append("</div>")

                content_parts.append("</div>")
                content_parts.append("<hr/>")
            content_parts.append("</div>")

        return "\n".join(content_parts)

    async def _get_kb_answer_content(self, answer: Dict[str, Any], full_response_data: Dict[str, Any]) -> str:
        """Extract KB answer content from translations in the response assets"""

        content_parts: List[str] = []

        assets = full_response_data.get('assets', {})
        translations = assets.get('KnowledgeBaseAnswerTranslation', {})
        translation_contents = assets.get('KnowledgeBaseAnswerTranslationContent', {})

        if translations:
            for trans_id, translation in translations.items():
                title: str = translation.get('title', '')
                trans_id_str = str(trans_id)
                content_obj = translation_contents.get(trans_id_str, {})
                content_body: str = content_obj.get('body', '')
                if not content_body:
                    content_body = translation.get('content', {}).get('body', '') if isinstance(translation.get('content'), dict) else translation.get('content', '')

                if title:
                    content_parts.append(f"<h1>{title}</h1>")
                if content_body:
                    content_html = self._absolutize_html_urls(content_body)
                    content_html = await self._inline_images_as_base64(content_html)
                    content_parts.append(content_html)

                if len(translations) > 1:
                    content_parts.append("<hr/>")
        else:
            content_parts.append("<p>No content available</p>")
        return "\n".join(content_parts)

    def _absolutize_html_urls(self, html: str) -> str:
        """Convert relative URLs in HTML to absolute using base_url"""
        if not html or not self.base_url:
            return html

        def _prefix_root(match: re.Match) -> str:
            attr = match.group('attr')
            quote = match.group('q')
            url = match.group('url')
            return f"{attr}={quote}{self.base_url}{url}{quote}"

        before = html
        html = re.sub(
            r"(?P<attr>\b(?:src|href))=(?P<q>[\"\'])(?P<url>/[^\"\']+)(?P=q)",
            _prefix_root,
            html,
            flags=re.X,
        )

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

    async def _inline_images_as_base64(self, html: str) -> str:
        """Inline <img> sources as base64 by fetching with authenticated client.
        """
        if not html:
            return html
        if not self.zammad_datasource:
            return html

        max_images_to_inline = 10
        max_bytes_per_image = 2 * 1024 * 1024
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
                if response.status >= HTTP_ERROR_STATUS_CODE:
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
