import base64
import html
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from logging import Logger

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from msgraph.generated.models.attachment import Attachment  # type: ignore
from msgraph.generated.models.conversation_thread import (
    ConversationThread,  # type: ignore
)
from msgraph.generated.models.group import Group  # type: ignore
from msgraph.generated.models.mail_folder import MailFolder  # type: ignore
from msgraph.generated.models.message import Message  # type: ignore
from msgraph.generated.models.post import Post  # type: ignore
from msgraph.generated.models.user import User  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
    RecordRelations,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
    generate_record_sync_point_key,
)
from app.connectors.core.constants import (
    AuthFieldKeys,
    BatchConfig,
    ConfigPaths,
    IconPaths,
    OAuthConfigKeys,
)
from app.connectors.core.registry.auth_builder import AuthBuilder, AuthType
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
    SyncStrategy,
)
from app.connectors.core.registry.filters import (
    DatetimeOperator,
    FilterCategory,
    FilterCollection,
    FilterField,
    FilterOperator,
    FilterOption,
    FilterOptionsResponse,
    FilterType,
    IndexingFilterKey,
    MultiselectOperator,
    OptionSourceType,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.core.registry.types import FieldType
from app.connectors.sources.microsoft.common.apps import OutlookApp
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.connectors.sources.microsoft.common.outlook_constants import (
    MessagesDeltaResult,
    OutlookAPIFields,
    OutlookConnectorNames,
    OutlookCredentials,
    OutlookDefaults,
    OutlookDocs,
    OutlookFilterKeys,
    OutlookFolders,
    OutlookHTTPDetails,
    OutlookMediaTypes,
    OutlookODataFields,
    OutlookSyncConfig,
    OutlookSyncPointKeys,
    OutlookThreadDetection,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    MailRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.microsoft.microsoft import (
    GraphMode,
    MSGraphClientWithClientIdSecretConfig,
)
from app.sources.client.microsoft.microsoft import (
    MSGraphClient as ExternalMSGraphClient,
)
from app.sources.external.microsoft.outlook.outlook import (
    OutlookCalendarContactsDataSource,
    OutlookCalendarContactsResponse,
    OutlookMailFoldersResponse,
)
from app.sources.external.microsoft.users_groups.users_groups import (
    UsersGroupsDataSource,
    UsersGroupsResponse,
)
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import (
    datetime_to_epoch_ms,
    get_epoch_timestamp_in_ms,
)


@ConnectorBuilder(OutlookConnectorNames.TEAM)\
    .in_group("Microsoft 365")\
    .with_description("Sync emails from Outlook")\
    .with_categories(["Email"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH_ADMIN_CONSENT).fields([
            AuthField(
                name=AuthFieldKeys.CLIENT_ID,
                display_name="Application (Client) ID",
                placeholder="Enter your Azure AD Application ID",
                description="The Application (Client) ID from Azure AD App Registration",
                field_type=FieldType.TEXT.value
            ),
            AuthField(
                name=AuthFieldKeys.CLIENT_SECRET,
                display_name="Client Secret",
                placeholder="Enter your Azure AD Client Secret",
                description="The Client Secret from Azure AD App Registration",
                field_type=FieldType.PASSWORD.value,
                is_secret=True
            ),
            AuthField(
                name=AuthFieldKeys.TENANT_ID,
                display_name="Directory (Tenant) ID",
                placeholder="Enter your Azure AD Tenant ID",
                description="The Directory (Tenant) ID from Azure AD",
                field_type=FieldType.TEXT.value
            ),
            AuthField(
                name=AuthFieldKeys.HAS_ADMIN_CONSENT,
                display_name="Has Admin Consent",
                description="Check if admin consent has been granted for the application",
                field_type=FieldType.CHECKBOX.value,
                required=True,
                default_value=False
            ),
        ])
    ])\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon("outlook"))
        .add_documentation_link(DocumentationLink(
            "Azure AD App Registration Setup",
            OutlookDocs.AZURE_AD_SETUP_URL,
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            "Pipeshub Documentation",
            OutlookDocs.PIPESHUB_DOCS_URL_TEAM,
            "pipeshub"
        ))
        .add_conditional_display(AuthFieldKeys.REDIRECT_URI, AuthFieldKeys.HAS_ADMIN_CONSENT, "equals", False)
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, OutlookSyncConfig.DEFAULT_SYNC_INTERVAL_MINUTES)
        .add_filter_field(FilterField(
            name=SyncFilterKey.FOLDERS.value,
            display_name="Standard Folders",
            description="Select standard Outlook folders to sync emails from.",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            option_source_type=OptionSourceType.STATIC,
            options=OutlookFolders.STANDARD_FOLDERS
        ))
        .add_filter_field(FilterField(
            name=SyncFilterKey.CUSTOM_FOLDERS.value,
            display_name="Custom Folders",
            description="Include custom/non-standard email folders",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.SYNC,
        ))
        .add_filter_field(FilterField(
            name=SyncFilterKey.USERS.value,
            display_name="Users",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description="Select specific users to sync. Leave empty to sync all users.",
            option_source_type=OptionSourceType.DYNAMIC,
            default_operator=MultiselectOperator.IN.value
        ))
        .add_filter_field(FilterField(
            name=SyncFilterKey.GROUPS.value,
            display_name="Groups",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description="Select mail groups to sync. "
            "Supports distribution lists, mail-enabled "
            "security groups, and Microsoft 365 groups. "
            "Leave empty to sync all users.",
            option_source_type=OptionSourceType.DYNAMIC,
            default_operator=MultiselectOperator.IN.value
        ))
        .add_filter_field(FilterField(
            name=SyncFilterKey.RECEIVED_DATE.value,
            display_name="Received Date",
            description="Filter emails by received date. Defaults to last 60 days.",
            filter_type=FilterType.DATETIME,
            category=FilterCategory.SYNC,
            default_operator=DatetimeOperator.LAST_90_DAYS.value,
            default_value=None  # For LAST_X_DAYS operators, value is not needed
        ))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .add_filter_field(FilterField(
            name=IndexingFilterKey.MAILS.value,
            display_name="Index Emails",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of email messages",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name=IndexingFilterKey.ATTACHMENTS.value,
            display_name="Index Attachments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of email attachments",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name=IndexingFilterKey.GROUP_CONVERSATIONS.value,
            display_name="Index Group Conversations",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of Microsoft 365 group conversations",
            default_value=True
        ))
    )\
    .build_decorator()
class OutlookConnector(BaseConnector):
    """Microsoft Outlook connector for syncing emails and attachments."""

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
    ) -> None:
        super().__init__(
            OutlookApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
        self.external_outlook_client: OutlookCalendarContactsDataSource | None = None
        self.external_users_client: UsersGroupsDataSource | None = None
        self.credentials: OutlookCredentials | None = None
        self.connector_id = connector_id

        # User cache for performance optimization
        self._user_cache: dict[str, str] = {}  # email -> source_user_id mapping
        self._user_cache_timestamp: int | None = None
        self._user_cache_ttl: int = 3600  # 1 hour TTL in seconds

        # Group cache for web URL construction
        self._group_cache: dict[str, dict[str, str]] = {}  # group_id -> {'mail': ..., 'mailNickname': ...}

        self.email_delta_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=self.data_entities_processor.org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=self.data_store_provider
        )

        self.group_conversations_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=self.data_entities_processor.org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=self.data_store_provider
        )

        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()


    async def init(self) -> bool:
        """Initialize the Outlook connector with credentials and Graph client."""
        try:

            connector_id = self.connector_id

            # Load credentials
            self.credentials = await self._get_credentials(connector_id)

            # Create shared MSGraph client - store as instance variable for proper cleanup
            self.external_client: ExternalMSGraphClient = ExternalMSGraphClient.build_with_config(
                MSGraphClientWithClientIdSecretConfig(
                    self.credentials.client_id,
                    self.credentials.client_secret,
                    self.credentials.tenant_id
                ),
                mode=GraphMode.APP
            )

            # Create both data source clients
            self.external_outlook_client = OutlookCalendarContactsDataSource(self.external_client)
            self.external_users_client = UsersGroupsDataSource(self.external_client)

            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize Outlook connector: {e}")
            return False

    async def test_connection_and_access(self) -> bool:
        """Test connection and access to external APIs."""
        try:
            if not self.external_outlook_client or not self.external_users_client or not self.credentials:
                return False

            if not (self.credentials.tenant_id and
                    self.credentials.client_id and
                    self.credentials.client_secret):
                return False

            try:
                # Get just 1 user with minimal fields to test connection
                response: UsersGroupsResponse = await self.external_users_client.users_user_list_user(
                    top=1,
                    select=OutlookAPIFields.USER_ID_SELECT_FIELDS
                )

                if not response.success:
                    self.logger.error(f"Connection test failed: {response.error}")
                    return False

                self.logger.info("✅ Outlook connector connection test passed")
                return True

            except Exception as api_error:
                self.logger.error(f"API connection test failed: {api_error}")
                return False

        except Exception as e:
            self.logger.error(f"Connection test failed: {e}")
            return False

    async def _get_credentials(self, connector_id: str) -> OutlookCredentials:
        """Load Outlook credentials from configuration."""
        try:
            config_path = ConfigPaths.CONNECTOR_CONFIG.format(connector_id=connector_id)
            config = await self.config_service.get_config(config_path)

            if not config:
                raise ValueError(f"Outlook configuration not found for connector {connector_id}")

            auth = config[OAuthConfigKeys.AUTH]
            return OutlookCredentials(
                tenant_id=auth[AuthFieldKeys.TENANT_ID],
                client_id=auth[AuthFieldKeys.CLIENT_ID],
                client_secret=auth[AuthFieldKeys.CLIENT_SECRET],
                has_admin_consent=auth.get(AuthFieldKeys.HAS_ADMIN_CONSENT, False),
            )
        except Exception as e:
            self.logger.error(f"Failed to load Outlook credentials for connector {connector_id}: {e}")
            raise

    async def _populate_user_cache(self) -> None:
        """Populate the user cache with email to source_user_id mappings."""
        try:
            current_time = int(datetime.now(timezone.utc).timestamp())

            # Check if cache is still valid
            if (self._user_cache_timestamp and
                current_time - self._user_cache_timestamp < self._user_cache_ttl and
                self._user_cache):
                return

            self.logger.info("Refreshing user cache...")
            all_users = await self._get_all_users_external()

            # Build the cache
            new_cache = {}
            for user in all_users:
                if user.email:
                    new_cache[user.email.lower()] = user.source_user_id

            self._user_cache = new_cache
            self._user_cache_timestamp = current_time
            self.logger.info(f"User cache refreshed with {len(self._user_cache)} users")

        except Exception as e:
            self.logger.error(f"Failed to populate user cache: {e}")

    async def _get_user_id_from_email(self, email: str) -> str | None:
        """Get user ID from email using cache."""
        try:
            # Ensure cache is populated
            await self._populate_user_cache()

            return self._user_cache.get(email.lower())
        except Exception as e:
            self.logger.error(f"Error getting user ID from cache for {email}: {e}")
            return None


    async def run_sync(self) -> None:
        """Run full Outlook sync - users, groups, emails and attachments."""
        try:
            org_id = self.data_entities_processor.org_id
            self.logger.info("Starting Outlook sync...")

            # Load filters from config service
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, OutlookFilterKeys.TEAM, self.connector_id, self.logger
            )

            # Ensure external clients are initialized
            if not self.external_outlook_client or not self.external_users_client:
                raise Exception("External API clients not initialized. Call init() first.")

            # Sync users and get list of users to process
            users_to_sync = await self._sync_users()

            # Sync Microsoft 365 groups
            synced_groups = await self._sync_user_groups()

            # Sync group conversations (pass synced groups)
            await self._sync_group_conversations(synced_groups)

            # Process emails per user
            async for status in self._process_users(org_id, users_to_sync):
                self.logger.info(status)

            self.logger.info("Outlook sync completed successfully")

        except Exception as e:
            self.logger.error(f"Error during Outlook sync: {e}")
            raise

    async def _sync_users(self) -> list[AppUser]:
        """Sync organization users and return active users to process."""
        try:
            self.logger.info("Syncing organization users...")

            # Get all users from Microsoft Graph
            all_enterprise_users = await self._get_all_users_external()

            # Get active users from database
            all_active_users = await self.data_entities_processor.get_all_active_users()

            # Create mapping of email to source_user_id
            email_to_source_id = {
                user.email.lower(): user.source_user_id
                for user in all_enterprise_users
                if user.email
            }

            # Filter active users that exist in enterprise (add source_user_id)
            users_to_sync = []
            for user in all_active_users:
                if user.email and user.email.lower() in email_to_source_id:
                    user.source_user_id = email_to_source_id[user.email.lower()]
                    users_to_sync.append(user)

            # Apply user filter
            users_filter = self.sync_filters.get(SyncFilterKey.USERS)
            has_users_filter = users_filter and not users_filter.is_empty()

            if has_users_filter:
                allowed_emails: set[str] = set()

                selected_emails = users_filter.get_value()
                allowed_emails.update(
                    e.lower() for e in selected_emails
                )

                filtered_users = [
                    user for user in users_to_sync
                    if user.email
                    and user.email.lower() in allowed_emails
                ]
                self.logger.info(
                    "Users filter applied: %d users selected "
                    "out of %d active users",
                    len(filtered_users),
                    len(users_to_sync),
                )
                users_to_sync = filtered_users

            # Populate user cache for performance
            await self._populate_user_cache()

            # Sync all enterprise users to database
            await self.data_entities_processor.on_new_app_users(all_enterprise_users)

            self.logger.info(f"✅ Synced {len(all_enterprise_users)} enterprise users, {len(users_to_sync)} active users to process")

            return users_to_sync

        except Exception as e:
            self.logger.error(f"Error syncing users: {e}")
            raise

    async def _get_all_users_external(self) -> list[AppUser]:
        """Get all users using external Users Groups API with pagination."""
        try:
            if not self.external_users_client:
                raise Exception("External Users Groups client not initialized")

            all_users = []
            next_url = None
            page_num = 1

            while True:
                response: UsersGroupsResponse = await self.external_users_client.users_user_list_user(
                    next_url=next_url
                )

                if not response.success or not response.data:
                    self.logger.error(f"Failed to get users page {page_num}: {response.error}")
                    break

                # response.data is UserCollectionResponse with .value containing list[User]
                user_data = response.data.value if response.data.value else []

                for user in user_data:
                    display_name = user.display_name or ''
                    given_name = user.given_name or ''
                    surname = user.surname or ''

                    full_name = display_name if display_name else f"{given_name} {surname}".strip()
                    if not full_name:
                        full_name = (
                            user.mail
                            or user.user_principal_name
                            or OutlookDefaults.UNKNOWN_USER_LABEL
                        )

                    app_user = AppUser(
                        app_name=Connectors.OUTLOOK,
                        connector_id=self.connector_id,
                        source_user_id=user.id,
                        email=user.mail or user.user_principal_name,
                        full_name=full_name
                    )
                    all_users.append(app_user)

                # Check for next page
                next_url = response.data.odata_next_link if response.data.odata_next_link else None
                if not next_url:
                    break

                page_num += 1

            self.logger.info(f"Retrieved {len(all_users)} total users across {page_num} page(s)")
            return all_users

        except Exception as e:
            self.logger.error(f"Error getting users from external API: {e}")
            return []

    async def _sync_user_groups(self) -> list[AppUserGroup]:
        """Sync Microsoft 365 groups and their memberships (full sync)."""
        try:
            self.logger.info("Starting Microsoft 365 groups synchronization...")

            if not self.external_users_client:
                raise Exception("External Users Groups client not initialized")

            # Get all groups (full sync, no delta)
            groups = await self._get_all_microsoft_365_groups()

            if not groups:
                self.logger.info("No Microsoft 365 groups found")
                return []

            self.logger.info(f"Found {len(groups)} Microsoft 365 groups to process")

            # Apply groups filter (IN / NOT_IN) based on group mail address
            groups_filter = self.sync_filters.get(SyncFilterKey.GROUPS)
            if groups_filter and not groups_filter.is_empty():
                total_before = len(groups)
                selected_mails = {m.lower() for m in groups_filter.get_value()}
                operator_str = groups_filter.get_operator().value

                if operator_str == FilterOperator.IN:
                    groups = [
                        g for g in groups
                        if g.mail and g.mail.lower() in selected_mails
                    ]
                elif operator_str == FilterOperator.NOT_IN:
                    groups = [
                        g for g in groups
                        if not g.mail or g.mail.lower() not in selected_mails
                    ]

                self.logger.info(
                    "Groups filter (%s) applied: %d groups selected out of %d",
                    operator_str, len(groups), total_before,
                )

            # Process groups in batches
            user_groups_batch: list[tuple[AppUserGroup, list[AppUser]]] = []
            group_record_groups_batch: list[tuple[RecordGroup, list]] = []
            all_synced_user_groups: list[AppUserGroup] = []
            batch_size = OutlookSyncConfig.USER_GROUPS_SYNC_BATCH_SIZE

            # groups are Pydantic Group objects from _get_all_microsoft_365_groups
            for group in groups:
                try:
                    # Check if group was deleted
                    additional_data = group.additional_data or {}
                    is_deleted = (additional_data.get('@removed', {}).get('reason') == 'deleted')

                    if is_deleted:
                        group_id = group.id
                        self.logger.info(f"Deleting group: {group_id}")
                        await self.data_entities_processor.on_user_group_deleted(
                            external_group_id=group_id,
                            connector_id=self.connector_id
                        )
                        continue

                    # Process add/update
                    group_id = group.id
                    group_name = group.display_name or OutlookDefaults.UNKNOWN_GROUP_LABEL

                    if not group_id:
                        continue

                    self.logger.debug(f"Processing group: {group_name} ({group_id})")

                    # Create AppUserGroup
                    user_group = AppUserGroup(
                        app_name=Connectors.OUTLOOK,
                        connector_id=self.connector_id,
                        source_user_group_id=group_id,
                        name=group_name,
                        org_id=self.data_entities_processor.org_id,
                        description=group.description,
                    )

                    # Cache group mail properties for URL construction
                    group_mail = group.mail
                    group_mail_nickname = group.mail_nickname
                    if group_id and group_mail and group_mail_nickname:
                        self._group_cache[group_id] = {
                            'mail': group_mail,
                            'mailNickname': group_mail_nickname
                        }

                    # Create RecordGroup for group mailbox
                    group_record_group = self._transform_group_to_record_group(group)

                    # Fetch members for this group
                    members = await self._get_group_members(group_id)

                    # Convert to AppUser objects - members are Pydantic User objects from _get_group_members
                    app_users = []
                    for member in members:
                        member_email = (member.mail or member.user_principal_name)
                        if member_email:
                            # Look up existing user from cache
                            user_id = self._user_cache.get(member_email.lower())
                            if user_id:
                                app_user = AppUser(
                                    app_name=Connectors.OUTLOOK,
                                    connector_id=self.connector_id,
                                    source_user_id=user_id,
                                    email=member_email,
                                    full_name=member.display_name or '',
                                )
                                app_users.append(app_user)

                    user_groups_batch.append((user_group, app_users))
                    all_synced_user_groups.append(user_group)

                    # Add group mailbox RecordGroup to batch with group permission
                    if group_record_group:
                        # Create group-level permission for the Microsoft 365 group
                        group_permission = Permission(
                            external_id=group_id,
                            type=PermissionType.READ,
                            entity_type=EntityType.GROUP
                        )
                        group_record_groups_batch.append((group_record_group, [group_permission]))

                    # Process batch if size reached
                    if len(user_groups_batch) >= batch_size:
                        await self.data_entities_processor.on_new_user_groups(user_groups_batch)
                        self.logger.info(f"Processed batch of {len(user_groups_batch)} groups")
                        user_groups_batch = []

                        # Also sync group mailbox RecordGroups
                        if group_record_groups_batch:
                            await self.data_entities_processor.on_new_record_groups(group_record_groups_batch)
                            group_record_groups_batch = []

                except Exception as e:
                    group_name = group.display_name or 'unknown'
                    self.logger.error(f"Error processing group {group_name}: {e}")
                    continue

            # Process remaining groups
            if user_groups_batch:
                await self.data_entities_processor.on_new_user_groups(user_groups_batch)
                self.logger.info(f"Processed final batch of {len(user_groups_batch)} groups")

            # Process remaining group mailbox RecordGroups
            if group_record_groups_batch:
                await self.data_entities_processor.on_new_record_groups(group_record_groups_batch)

            self.logger.info(f"✅ Synced {len(all_synced_user_groups)} Microsoft 365 groups")

            # Return synced AppUserGroups for conversation sync
            return all_synced_user_groups

        except Exception as e:
            self.logger.error(f"Error syncing user groups: {e}", exc_info=True)
            raise

    async def _get_all_microsoft_365_groups(self) -> list[Group]:
        """Get all Microsoft 365 groups with pagination."""
        try:
            if not self.external_users_client:
                raise Exception("External Users Groups client not initialized")

            all_groups = []
            next_url = None
            page_num = 1

            while True:
                response = await self.external_users_client.groups_list_groups(
                    next_url=next_url,
                    select=OutlookAPIFields.GROUP_SELECT_FIELDS
                )

                if not response.success:
                    self.logger.error(f"Failed to get groups page {page_num}: {response.error}")
                    break

                # response.data is GroupCollectionResponse with .value containing list[Group]
                groups_page = response.data.value if response.data.value else []
                all_groups.extend(groups_page)

                # Check for next page
                next_url = response.data.odata_next_link if response.data.odata_next_link else None
                if not next_url:
                    break

                page_num += 1

            # Filter: Microsoft 365 (Unified) groups with a mailbox
            microsoft_365_groups = [
                group for group in all_groups
                if group.group_types and 'Unified' in group.group_types
                and group.mail_enabled is True
            ]

            self.logger.info(f"Retrieved {len(all_groups)} total groups across {page_num} page(s), filtered to {len(microsoft_365_groups)} Microsoft 365 groups with mailbox")
            return microsoft_365_groups

        except Exception as e:
            self.logger.error(f"Error getting Microsoft 365 groups: {e}", exc_info=True)
            return []

    async def _get_group_members(self, group_id: str) -> list[User]:
        """Get members of a specific group with pagination."""
        try:
            if not self.external_users_client:
                raise Exception("External Users Groups client not initialized")

            all_members = []
            next_url = None
            page_num = 1

            while True:
                response = await self.external_users_client.groups_list_transitive_members(
                    group_id=group_id,
                    next_url=next_url,
                    select=OutlookAPIFields.GROUP_MEMBER_SELECT_FIELDS
                )

                if not response.success:
                    self.logger.error(f"Failed to get members page {page_num} for group {group_id}: {response.error}")
                    break

                # response.data is DirectoryObjectCollectionResponse with .value containing list[User]
                members_page = response.data.value if response.data.value else []
                all_members.extend(members_page)

                # Check for next page
                next_url = response.data.odata_next_link if response.data.odata_next_link else None
                if not next_url:
                    break

                page_num += 1

            return all_members

        except Exception as e:
            self.logger.error(f"Error getting group members for {group_id}: {e}")
            return []

    async def _get_user_groups(self, user_id: str) -> list[Group]:
        """Get groups that a user is a member of (cached for performance).

        Returns:
            List of Pydantic Group objects
        """
        try:
            if not self.external_users_client:
                return []

            # Use the existing groups_list_member_of method
            response = await self.external_users_client.groups_list_member_of(
                group_id=user_id,  # Note: This method name is misleading, it actually takes user_id
                select=OutlookAPIFields.USER_GROUP_SELECT_FIELDS
            )

            if not response.success:
                return []

            # response.data is DirectoryObjectCollectionResponse with .value containing list[Group]
            return response.data.value if response.data.value else []

        except Exception as e:
            self.logger.error(f"Error getting user groups for {user_id}: {e}")
            return []

    def _transform_group_to_record_group(self, group: Group) -> RecordGroup | None:
        """
        Transform Microsoft 365 group to RecordGroup entity for group mailbox.

        Args:
            group: Pydantic Group object from Microsoft Graph API

        Returns:
            RecordGroup object or None if transformation fails
        """
        try:
            # group is a Pydantic Group object
            group_id = group.id
            group_name = group.display_name or OutlookDefaults.UNKNOWN_GROUP_LABEL

            if not group_id:
                self.logger.warning("Group has no ID, skipping RecordGroup creation")
                return None

            # Get group description and mail
            group_mail = group.mail or ''

            # Create simple description
            description = f"{group_name} group mailbox"
            if group_mail:
                description += f" ({group_mail})"

            # Get timestamps if available
            created_at = datetime_to_epoch_ms(group.created_date_time)

            return RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=group_name,
                short_name=group_name,
                description=description,
                external_group_id=group_id,
                parent_external_group_id=None,
                connector_name=Connectors.OUTLOOK,
                connector_id=self.connector_id,
                group_type=RecordGroupType.GROUP_MAILBOX,
                web_url=None,
                source_created_at=created_at,
                source_updated_at=created_at,
            )


        except Exception as e:
            self.logger.error(f"Error transforming group to RecordGroup: {e}")
            return None

    async def _sync_group_conversations(self, user_groups: list[AppUserGroup]) -> None:
        """Sync conversations from all Microsoft 365 group mailboxes."""
        try:
            self.logger.info("Starting group conversations synchronization...")

            if not self.external_outlook_client:
                raise Exception("External Outlook client not initialized")

            if not user_groups:
                self.logger.info("No groups provided for conversation sync")
                return

            self.logger.info(f"Syncing conversations for {len(user_groups)} groups")

            total_conversations = 0
            for group in user_groups:
                try:
                    count = await self._sync_single_group_conversations(group)
                    total_conversations += count
                except Exception as e:
                    self.logger.error(f"Error syncing conversations for group {group.name}: {e}")
                    continue

            self.logger.info(f"✅ Synced {total_conversations} group conversation posts across {len(user_groups)} groups")

        except Exception as e:
            self.logger.error(f"Error syncing group conversations: {e}", exc_info=True)
            raise

    async def _sync_single_group_conversations(self, group: AppUserGroup) -> int:
        """Sync conversations for a single group with incremental thread filtering."""
        try:
            group_id = group.source_user_group_id
            group_name = group.name
            org_id = self.data_entities_processor.org_id

            self.logger.info(f"Syncing conversations for group: {group_name}")

            # Get sync point for this group (tracks last thread sync)
            sync_point_key = generate_record_sync_point_key(
                OutlookSyncPointKeys.RECORD_TYPE_GROUP_CONVERSATIONS,
                OutlookSyncPointKeys.SEGMENT_GROUP,
                group_id
            )
            sync_point = await self.group_conversations_sync_point.read_sync_point(sync_point_key)
            last_sync_timestamp = sync_point.get('last_sync_timestamp') if sync_point else None

            # Convert timestamp to correct format if needed
            if last_sync_timestamp:
                try:
                    dt = datetime.fromisoformat(last_sync_timestamp.replace('Z', '+00:00'))
                    last_sync_timestamp = dt.strftime('%Y-%m-%dT%H:%M:%S') + 'Z'
                except (ValueError, AttributeError) as e:
                    self.logger.warning(f"Failed to parse timestamp '{last_sync_timestamp}': {e}. Will perform full sync for this group.")
                    last_sync_timestamp = None

            # Get threads updated since last sync (server-side filter)
            threads = await self._get_group_threads(group_id, last_sync_timestamp)

            if not threads:
                self.logger.debug(f"No updated threads found for group {group_name}")
                return 0

            self.logger.info(f"Found {len(threads)} updated threads for group {group_name}")

            total_posts = 0
            for thread in threads:
                try:
                    # Get all posts for this thread, filter client-side
                    posts_count = await self._process_group_thread(org_id, group, thread, last_sync_timestamp)
                    total_posts += posts_count
                except Exception as e:
                    # threads are Pydantic ConversationThread objects from _get_group_threads
                    thread_id = thread.id if hasattr(thread, 'id') else 'unknown'
                    self.logger.error(f"Error processing thread {thread_id}: {e}")
                    continue

            # Update group sync point with current timestamp
            current_timestamp = datetime.now(timezone.utc)
            timestamp_str = current_timestamp.strftime('%Y-%m-%dT%H:%M:%S') + 'Z'

            await self.group_conversations_sync_point.update_sync_point(
                sync_point_key,
                {
                    'last_sync_timestamp': timestamp_str,
                    'group_id': group_id,
                    'group_name': group_name
                }
            )

            return total_posts

        except Exception as e:
            self.logger.error(f"Error syncing conversations for group {group.name}: {e}")
            return 0

    async def _get_group_threads(self, group_id: str, last_sync_timestamp: str | None = None) -> list[ConversationThread]:
        """Get threads for a group, filtered by last sync timestamp if provided.

        Returns:
            List of Pydantic ConversationThread objects
        """
        try:
            if not self.external_outlook_client:
                raise Exception("External Outlook client not initialized")

            # Build filter for threads
            filter_str = None
            if last_sync_timestamp:
                filter_str = f"lastDeliveredDateTime ge {last_sync_timestamp}"

            response = await self.external_outlook_client.groups_list_threads(
                group_id=group_id,
                select=OutlookAPIFields.THREAD_SELECT_FIELDS,
                filter=filter_str
            )

            if not response.success:
                self.logger.error(f"Failed to get threads for group {group_id}: {response.error}")
                return []

            # response.data is ConversationThreadCollectionResponse with .value containing list[ConversationThread]
            return response.data.value if response.data.value else []

        except Exception as e:
            self.logger.error(f"Error getting threads for group {group_id}: {e}")
            return []

    async def _process_group_thread(self, org_id: str, group: AppUserGroup, thread : ConversationThread, last_sync_timestamp: str | None = None) -> int:
        """Process a single thread and its posts with client-side post filtering.

        Args:
            thread: Pydantic ConversationThread object
        """
        try:
            group_id = group.source_user_group_id
            # thread is a Pydantic ConversationThread object from _get_group_threads
            thread_id = thread.id

            if not thread_id:
                return 0

            # Parse last sync time for comparison
            last_sync_time = None
            if last_sync_timestamp:
                try:
                    last_sync_time = datetime.fromisoformat(last_sync_timestamp.replace('Z', '+00:00'))
                except Exception as e:
                    self.logger.warning(f"Failed to parse sync timestamp: {e}")

            # Get ALL posts in the thread (API doesn't support filtering)
            all_posts = await self._get_thread_posts(group_id, thread_id)

            if not all_posts:
                return 0

            # Filter posts client-side - only process new ones
            posts_to_process = []

            # all_posts are Pydantic Post objects from _get_thread_posts
            for post in all_posts:
                post_received = post.received_date_time
                if post_received:
                    try:
                        if isinstance(post_received, str):
                            post_time = datetime.fromisoformat(post_received.replace('Z', '+00:00'))
                        else:
                            post_time = post_received

                        # Include post if it's new (after last sync)
                        if not last_sync_time or post_time > last_sync_time:
                            posts_to_process.append(post)
                    except Exception as e:
                        self.logger.warning(f"Error parsing post time: {e}")
                        # Include post if we can't parse time (safer)
                        posts_to_process.append(post)
                else:
                    # No timestamp - include it (safer)
                    posts_to_process.append(post)

            if not posts_to_process:
                return 0

            self.logger.debug(f"Processing {len(posts_to_process)} new posts out of {len(all_posts)} total")

            # Process each new post as a MailRecord
            batch_records = []
            for post in posts_to_process:
                try:
                    record_update = await self._process_group_post(org_id, group, thread, post)
                    if record_update and record_update.record:
                        permissions = record_update.new_permissions or []
                        batch_records.append((record_update.record, permissions))

                        # Process attachments if post has them
                        has_attachments = post.has_attachments or False
                        if has_attachments:
                            attachment_updates = await self._process_group_post_attachments(
                                org_id, group, thread, post, permissions
                            )
                            if attachment_updates:
                                batch_records.extend(attachment_updates)
                except Exception as e:
                    post_id = post.id if hasattr(post, 'id') else 'unknown'
                    self.logger.error(f"Error processing post {post_id}: {e}")
                    continue

            # Save batch
            if batch_records:
                await self.data_entities_processor.on_new_records(batch_records)

            return len(batch_records)

        except Exception as e:
            self.logger.error(f"Error processing thread: {e}")
            return 0

    async def _get_thread_posts(self, group_id: str, thread_id: str) -> list[Post]:
        """Get all posts in a thread.

        Returns:
            List of Pydantic Post objects
        """
        try:
            if not self.external_outlook_client:
                raise Exception("External Outlook client not initialized")

            response = await self.external_outlook_client.groups_threads_list_posts(
                group_id=group_id,
                thread_id=thread_id,
                select=OutlookAPIFields.POST_SELECT_FIELDS
            )

            if not response.success:
                self.logger.error(f"Failed to get posts for thread {thread_id}: {response.error}")
                return []

            # response.data is PostCollectionResponse with .value containing list[Post]
            return response.data.value if response.data.value else []

        except Exception as e:
            self.logger.error(f"Error getting posts for thread {thread_id}: {e}")
            return []

    async def _process_group_post(
        self,
        org_id: str,
        group: AppUserGroup,
        thread: ConversationThread,
        post: Post
    ) -> RecordUpdate | None:
        """Process a single group post as a MailRecord.

        Args:
            thread: Pydantic ConversationThread object
            post: Pydantic Post object
        """
        try:
            # post and thread are Pydantic objects
            post_id = post.id

            # Check if record exists
            existing_record = await self._get_existing_record(org_id, post_id)
            is_new = existing_record is None
            is_updated = False

            if not is_new:
                # Check if post was updated (no etag for posts, use receivedDateTime)
                current_received = post.received_date_time
                if current_received and existing_record.source_updated_at:
                    current_ts = datetime_to_epoch_ms(current_received)
                    if current_ts and current_ts > existing_record.source_updated_at:
                        is_updated = True

            record_id = existing_record.id if existing_record else str(uuid.uuid4())

            # Extract sender
            from_obj = post.from_
            sender_email = self._extract_email_from_recipient(from_obj) if from_obj else ''

            # Get thread topic as subject
            thread_topic = thread.topic

            # Get thread ID from the thread object
            thread_id = thread.id

            # Group conversations don't have individual recipients - access is controlled by group membership
            to_emails = []
            cc_emails = []

            # Construct web URL for group conversation
            group_id = group.source_user_group_id
            weburl = await self._construct_group_mail_weburl(group_id)

            # Create MailRecord for the post
            mail_record = MailRecord(
                id=record_id,
                org_id=org_id,
                record_name=thread_topic,
                record_type=RecordType.GROUP_MAIL,
                external_record_id=post_id,
                version=0 if is_new else existing_record.version + 1,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.OUTLOOK,
                connector_id=self.connector_id,
                source_created_at=datetime_to_epoch_ms(post.received_date_time),
                source_updated_at=datetime_to_epoch_ms(post.received_date_time),
                weburl=weburl,
                mime_type=MimeTypes.HTML.value,
                external_record_group_id=group_id,
                record_group_type=RecordGroupType.GROUP_MAILBOX,
                subject=thread_topic,
                from_email=sender_email,
                to_emails=to_emails,
                cc_emails=cc_emails,
                bcc_emails=[],
                thread_id=thread_id,
                is_parent=False,
                internet_message_id='',
                conversation_index='',
            )

            # Apply indexing filter
            if not self.indexing_filters.is_enabled(IndexingFilterKey.GROUP_CONVERSATIONS, default=True):
                mail_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

            # Create group-level permission
            permission = Permission(
                external_id=group.source_user_group_id,
                type=PermissionType.READ,
                entity_type=EntityType.GROUP,
            )

            return RecordUpdate(
                record=mail_record,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=False,
                metadata_changed=False,
                content_changed=is_updated,
                permissions_changed=True,
                new_permissions=[permission],
                external_record_id=post_id,
            )

        except Exception as e:
            self.logger.error(f"Error processing group post: {e}")
            return None

    async def _process_group_post_attachments(
        self,
        org_id: str,
        group: AppUserGroup,
        thread: ConversationThread,
        post: Post,
        post_permissions: list[Permission]
    ) -> list[tuple[Record, list[Permission]]]:
        """Process attachments for a group post.

        Args:
            thread: Pydantic ConversationThread object
            post: Pydantic Post object
        """
        try:
            group_id = group.source_user_group_id
            # post is a Pydantic Post object
            thread_id = post.conversation_thread_id
            post_id = post.id

            if not thread_id:
                self.logger.warning(f"No thread_id for post {post_id}")
                return []

            attachments = await self._get_group_post_attachments(group_id, thread_id, post_id)

            if not attachments:
                return []

            attachment_records = []

            # attachments are Pydantic Attachment objects from _get_group_post_attachments
            for attachment in attachments:
                try:
                    attachment_id = attachment.id
                    existing_record = await self._get_existing_record(org_id, attachment_id)

                    content_type = attachment.content_type
                    if not content_type:
                        continue

                    is_new = existing_record is None
                    record_id = existing_record.id if existing_record else str(uuid.uuid4())

                    file_name = attachment.name or OutlookDefaults.ATTACHMENT_NAME
                    extension = None
                    if '.' in file_name:
                        extension = file_name.split('.')[-1].lower()

                    attachment_record = FileRecord(
                        id=record_id,
                        org_id=org_id,
                        record_name=file_name,
                        record_type=RecordType.FILE,
                        external_record_id=attachment_id,
                        version=0 if is_new else existing_record.version + 1,
                        origin=OriginTypes.CONNECTOR,
                        connector_name=Connectors.OUTLOOK,
                        connector_id=self.connector_id,
                        source_created_at=datetime_to_epoch_ms(attachment.last_modified_date_time),
                        source_updated_at=datetime_to_epoch_ms(attachment.last_modified_date_time),
                        mime_type=self._get_mime_type_enum(content_type),
                        parent_external_record_id=post_id,
                        parent_record_type=RecordType.GROUP_MAIL,
                        external_record_group_id=group_id,
                        record_group_type=RecordGroupType.GROUP_MAILBOX,
                        weburl=None,
                        is_file=True,
                        size_in_bytes=attachment.size or 0,
                        extension=extension,
                    )

                    if not self.indexing_filters.is_enabled(IndexingFilterKey.ATTACHMENTS, default=True):
                        attachment_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

                    attachment_records.append((attachment_record, post_permissions))

                except Exception as e:
                    self.logger.error(f"Error processing group post attachment: {e}")
                    continue

            return attachment_records

        except Exception as e:
            self.logger.error(f"Error processing attachments for post: {e}")
            return []

    async def _get_group_post_attachments(self, group_id: str, thread_id: str, post_id: str) -> list[Attachment]:
        """Get attachments for a group post.

        Returns:
            List of Pydantic Attachment objects
        """
        try:
            if not self.external_outlook_client:
                raise Exception("External Outlook client not initialized")

            response = await self.external_outlook_client.groups_threads_posts_list_attachments(
                group_id=group_id,
                conversationThread_id=thread_id,
                post_id=post_id
            )

            if not response.success:
                self.logger.error(f"Failed to get attachments for post {post_id}: {response.error}")
                return []

            # response.data is AttachmentCollectionResponse with .value containing list[Attachment]
            return response.data.value if response.data.value else []

        except Exception as e:
            self.logger.error(f"Error getting group post attachments: {e}")
            return []

    async def _download_group_post_attachment(
        self, group_id: str, thread_id: str, post_id: str, attachment_id: str
    ) -> bytes:
        """Download attachment content from a group post."""
        try:
            if not self.external_outlook_client:
                raise Exception("External Outlook client not initialized")

            response = await self.external_outlook_client.groups_threads_posts_get_attachments(
                group_id=group_id,
                conversationThread_id=thread_id,
                post_id=post_id,
                attachment_id=attachment_id
            )

            if not response.success or not response.data:
                return b''

            # response.data is a Pydantic FileAttachment object
            attachment_data = response.data

            # Extract content_bytes from Pydantic object
            content_bytes = attachment_data.content_bytes if attachment_data else None

            if not content_bytes:
                return b''

            return base64.b64decode(content_bytes)

        except Exception as e:
            self.logger.error(f"Error downloading group post attachment: {e}")
            return b''

    async def _process_users(self, org_id: str, users: list[AppUser]) -> AsyncGenerator[str, None]:
        """Process users sequentially."""
        for i, user in enumerate(users):
            self.logger.info(f"Processing user {i+1}/{len(users)}: {user.email}")
            try:
                # Process emails from all folders (includes folder discovery)
                email_result = await self._process_user_emails(org_id, user)
                yield f"User {i+1}/{len(users)}: {email_result}"
            except Exception as e:
                self.logger.error(f"Error processing user {user.email}: {e}")
                yield f"User {i+1}/{len(users)}: Failed - {str(e)}"

    async def _process_user_emails(self, org_id: str, user: AppUser) -> str:
        """Process emails from all folders sequentially."""
        try:
            # Sync folders as RecordGroups and get folder data for email processing
            folders = await self._sync_user_folders(user)

            if not folders:
                return f"No folders found for {user.email}"

            total_processed = 0
            folder_results = []
            all_mail_records = []  # Collect all mail records for email thread edges processing

            # Process folders sequentially instead of concurrently
            # folders are MailFolder Pydantic objects from _sync_user_folders
            for folder in folders:
                folder_name = folder.display_name or OutlookDefaults.FOLDER_NAME
                try:
                    result, folder_mail_records = await self._process_single_folder_messages(org_id, user, folder)
                    folder_results.append(f"{folder_name}: {result} messages")
                    total_processed += result
                    all_mail_records.extend(folder_mail_records)  # Collect mail records
                except Exception as e:
                    self.logger.error(f"Error processing folder {folder_name}: {e}")
                    folder_results.append(f"{folder_name}: Failed")

            # After all folders are processed, create email thread edges using collected records
            try:
                thread_edges_created = await self._create_all_thread_edges_for_user(org_id, user, all_mail_records)
                if thread_edges_created > 0:
                    self.logger.info(f"Created {thread_edges_created} thread edges for user {user.email}")
            except Exception as e:
                self.logger.error(f"Error creating thread edges for user {user.email}: {e}")

            return f"Processed {total_processed} items across {len(folders)} folders: {'; '.join(folder_results)}"

        except Exception as e:
            self.logger.error(f"Error processing all folders for user {user.email}: {e}")
            return f"Failed to process folders for {user.email}: {str(e)}"

    async def _find_parent_by_conversation_index_from_db(self, conversation_index: str, thread_id: str, org_id: str, user: AppUser) -> str | None:
        """Find parent message ID using conversation index by searching ArangoDB."""
        if not conversation_index:
            self.logger.debug(f"No conversation_index provided for thread {thread_id}")
            return None

        try:
            # Decode conversation index
            index_bytes = base64.b64decode(conversation_index)

            # Root message (22 bytes) has no parent
            if len(index_bytes) <= OutlookThreadDetection.ROOT_CONVERSATION_INDEX_LENGTH:
                return None

            # Get parent index by removing last 5 bytes
            parent_bytes = index_bytes[:-OutlookThreadDetection.CHILD_INDEX_SUFFIX_LENGTH]
            parent_index = base64.b64encode(parent_bytes).decode('utf-8')
            self.logger.debug(f"Thread {thread_id}: Looking for parent with conversation_index={parent_index}")

            # Search in ArangoDB for parent message
            async with self.data_store_provider.transaction() as tx_store:
                parent_record = await tx_store.get_record_by_conversation_index(
                    connector_id=self.connector_id,
                    conversation_index=parent_index,
                    thread_id=thread_id,
                    org_id=org_id,
                    user_id=user.user_id
                )

                if parent_record:
                    return parent_record.id
                else:
                    return None

        except Exception as e:
            self.logger.error(f"Error finding parent by conversation index from DB for thread {thread_id}: {e}")
            return None

    async def _create_all_thread_edges_for_user(self, org_id: str, user: AppUser, user_mail_records: list[Record]) -> int:
        """Create thread edges for all email messages of a user by searching ArangoDB for parents."""
        try:
            if not user_mail_records:
                self.logger.debug(f"No mail records provided for user {user.email}")
                return 0

            edges = []
            processed_count = 0

            # Process each mail record to find its parent
            for record in user_mail_records:
                if (hasattr(record, 'conversation_index') and record.conversation_index and
                    hasattr(record, 'thread_id') and record.thread_id):

                    # Find parent using ArangoDB lookup
                    parent_id = await self._find_parent_by_conversation_index_from_db(
                        record.conversation_index,
                        record.thread_id,
                        org_id,
                        user
                    )

                    if parent_id:
                        edge = {
                            "from_id": parent_id,
                            "from_collection": CollectionNames.RECORDS.value,
                            "to_id": record.id,
                            "to_collection": CollectionNames.RECORDS.value,
                            "relationType": RecordRelations.SIBLING.value
                        }
                        edges.append(edge)
                        processed_count += 1

            # Create all edges in batch
            if edges:
                try:
                    async with self.data_store_provider.transaction() as tx_store:
                        await tx_store.batch_create_edges(edges, collection=CollectionNames.RECORD_RELATIONS.value)
                except Exception as e:
                    self.logger.error(f"Error creating thread edges batch for user {user.email}: {e}")
                    processed_count = 0

            return processed_count

        except Exception as e:
            self.logger.error(f"Error creating all thread edges for user {user.email}: {e}")
            return 0

    def _determine_folder_filter_strategy(self) -> tuple[list[str] | None, str | None]:
        """Determine the folder filtering strategy based on user's filter selections.

        Retrieves filter settings and determines the appropriate filtering strategy:

        5 Scenarios:
        1. Nothing selected + custom enabled → Sync ALL folders (standard + custom)
        2. Nothing selected + custom disabled → Sync ONLY standard folders
        3. Standard folders selected + custom disabled → Sync ONLY selected standard folders
        4. Standard folders selected + custom enabled → Sync selected standard + ALL custom folders
        5. All standard folders selected + custom enabled → Sync ALL folders

        Returns:
            Tuple of (folder_names, filter_mode):
            - (None, None) = No filter, sync all folders
            - (list, "include") = Sync only these folders
            - (list, "exclude") = Sync all except these folders
        """
        # Get selected standard folders from filter
        selected_folders = []
        folders_filter = self.sync_filters.get(SyncFilterKey.FOLDERS)
        if folders_filter and not folders_filter.is_empty():
            selected_folders = folders_filter.get_value()

        # Get sync_custom_folders boolean (default: True)
        sync_custom_folders = True
        sync_custom_folders_filter = self.sync_filters.get(SyncFilterKey.CUSTOM_FOLDERS)
        if sync_custom_folders_filter and not sync_custom_folders_filter.is_empty():
            sync_custom_folders = sync_custom_folders_filter.get_value()

        # Determine strategy
        has_selection = bool(selected_folders)

        if not has_selection:
            # No folders selected - behavior depends on sync_custom_folders
            if sync_custom_folders:
                # Scenario 1: Sync everything (default behavior)
                self.logger.info("No folders selected, custom enabled - syncing all folders")
                return None, None
            else:
                # Scenario 2: Sync only standard folders
                self.logger.info("No folders selected, custom disabled - syncing only standard folders")
                return OutlookFolders.STANDARD_FOLDERS, "include"

        if not sync_custom_folders:
            # Scenario 3: Only selected standard folders
            self.logger.info(f"Syncing only selected standard folders: {selected_folders}")
            return selected_folders, "include"

        # Custom folders are enabled and some standard folders are selected
        all_standard_selected = set(selected_folders) == set(OutlookFolders.STANDARD_FOLDERS)

        if all_standard_selected:
            # Scenario 4: All standard folders + custom = everything
            self.logger.info("All standard folders selected + custom enabled - syncing all folders")
            return None, None

        # Scenario 5: Selected standard + all custom folders
        # Strategy: Exclude the non-selected standard folders
        non_selected = [f for f in OutlookFolders.STANDARD_FOLDERS if f not in selected_folders]
        self.logger.info(
            f"Syncing selected standard folders {selected_folders} + all custom folders "
            f"(excluding non-selected standard: {non_selected})"
        )
        return non_selected, "exclude"

    async def _get_child_folders_recursive(
        self,
        user_id: str,
        parent_folder: MailFolder
    ) -> list[MailFolder]:
        """Recursively get all child folders of a parent folder.

        Args:
            user_id: User identifier
            parent_folder: Parent MailFolder Pydantic object

        Returns:
            Flattened list of all child folders (including nested children)
        """
        try:
            # parent_folder is a MailFolder Pydantic object
            parent_folder_id = parent_folder.id
            parent_folder_name = parent_folder.display_name or OutlookDefaults.UNKNOWN_FOLDER_LABEL

            if not parent_folder_id:
                return []

            # Check if folder has children
            child_folder_count = parent_folder.child_folder_count or 0
            if child_folder_count == 0:
                self.logger.debug(f"Folder '{parent_folder_name}' has no child folders")
                return []

            # Fetch child folders using the API
            if not self.external_outlook_client:
                raise Exception("External Outlook client not initialized")

            response: OutlookMailFoldersResponse = await self.external_outlook_client.users_mail_folders_list_child_folders(
                user_id=user_id,
                mailFolder_id=parent_folder_id
            )

            if not response.success:
                self.logger.warning(
                    f"Failed to get child folders for '{parent_folder_name}': {response.error}"
                )
                return []

            # response.data is MailFolderCollectionResponse with .value containing list[MailFolder]
            child_folders = response.data.value if response.data.value else []

            if not child_folders:
                return []

            self.logger.info(
                f"Found {len(child_folders)} child folder(s) under '{parent_folder_name}'"
            )

            # Recursively process each child folder
            all_descendants = []
            for child in child_folders:
                all_descendants.append(child)
                # Recursively get grandchildren
                grandchildren = await self._get_child_folders_recursive(user_id, child)
                all_descendants.extend(grandchildren)

            return all_descendants

        except Exception as e:
            parent_name = parent_folder.display_name if parent_folder else OutlookDefaults.UNKNOWN_FOLDER_LABEL
            self.logger.error(f"Error getting child folders for '{parent_name}': {e}")
            return []

    async def _get_all_folders_for_user(
        self,
        user_id: str,
        folder_names: list[str] | None = None,
        folder_filter_mode: str | None = None
    ) -> tuple[list[MailFolder], set[str]]:
        """Get all folders for a user with optional filtering and nested folder support.

        Args:
            user_id: User identifier
            folder_names: Optional list of folder display names to filter
            folder_filter_mode: 'include' to whitelist or 'exclude' to blacklist folder_names

        Returns:
            Tuple of (folders, top_level_folder_ids):
                - folders: List of MailFolder Pydantic objects (includes nested folders by default)
                - top_level_folder_ids: Set of folder IDs that are top-level
        """
        try:
            if not self.external_outlook_client:
                raise Exception("External Outlook client not initialized")

            # Paginate through all top-level folders using cursor-based pagination
            top_level_folders = []
            next_url = None
            page_num = 1
            page_size = OutlookSyncConfig.FOLDER_PAGE_SIZE

            while True:
                # Get folders page with API-level filtering
                if next_url:
                    response: OutlookMailFoldersResponse = await self.external_outlook_client.users_list_mail_folders(
                        user_id=user_id,
                        next_url=next_url
                    )
                else:
                    response: OutlookMailFoldersResponse = await self.external_outlook_client.users_list_mail_folders(
                        user_id=user_id,
                        folder_names=folder_names,
                        folder_filter_mode=folder_filter_mode,
                        top=page_size
                    )

                if not response.success:
                    self.logger.error(f"Failed to get folders page {page_num}: {response.error}")
                    break

                # response.data is MailFolderCollectionResponse with .value containing list[MailFolder]
                raw_folders = response.data.value if response.data.value else []
                top_level_folders.extend(raw_folders)

                # Check for next page
                next_url = response.data.odata_next_link if response.data.odata_next_link else None
                if not next_url:
                    break

                page_num += 1

            self.logger.info(f"Retrieved {len(top_level_folders)} top-level folders across {page_num} page(s)")

            # Always include nested folders (no filter needed, it's always enabled)
            # Track top-level folder IDs to avoid storing parent references for them
            # This is required to differentiate between top-level folders and nested folders
            top_level_folder_ids = {folder.id for folder in top_level_folders if folder.id}

            all_folders = []
            for folder in top_level_folders:
                all_folders.append(folder)
                # Recursively get child folders
                child_folders = await self._get_child_folders_recursive(user_id, folder)
                all_folders.extend(child_folders)

            total_nested = len(all_folders) - len(top_level_folders)
            if total_nested > 0:
                self.logger.info(
                    f"Total: {len(top_level_folders)} top-level + "
                    f"{total_nested} nested = {len(all_folders)} total folders"
                )
            else:
                self.logger.info(f"Total: {len(all_folders)} folders (no nested folders found)")

            return all_folders, top_level_folder_ids

        except Exception as e:
            self.logger.error(f"Error getting folders for user {user_id}: {e}")
            return [], set()

    def _transform_folder_to_record_group(
        self,
        folder: MailFolder,
        user: AppUser,
        is_top_level: bool = False
    ) -> RecordGroup | None:
        """
        Transform Outlook mail folder to RecordGroup entity.

        Args:
            folder: MailFolder Pydantic object from Microsoft Graph API
            user: AppUser who owns this mailbox
            is_top_level: Whether this is a top-level folder (no parent should be stored)

        Returns:
            RecordGroup object or None if transformation fails
        """
        try:
            # folder is a MailFolder Pydantic object
            folder_id = folder.id
            folder_name = folder.display_name or OutlookDefaults.FOLDER_NAME

            if not folder_id:
                return None

            # Get parent folder ID for hierarchy
            # Top-level folders should not store parent_external_group_id even if API returns it
            parent_folder_id = None if is_top_level else folder.parent_folder_id

            # Create simple description
            description = f"{folder_name} folder for {user.email}"

            return RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=folder_name,
                short_name=folder_name,
                description=description,
                external_group_id=folder_id,
                parent_external_group_id=parent_folder_id,
                connector_name=Connectors.OUTLOOK,
                connector_id=self.connector_id,
                group_type=RecordGroupType.MAILBOX,
                web_url=None,
                source_created_at=None,
                source_updated_at=None,
            )

        except Exception as e:
            self.logger.error(f"Error transforming folder to RecordGroup: {e}")
            return None

    async def _sync_user_folders(self, user: AppUser) -> list[MailFolder]:
        """
        Sync mail folders for a user as RecordGroup entities and return folder data.

        Args:
            user: AppUser whose folders to sync

        Returns:
            List of MailFolder Pydantic objects (for email processing)
        """
        try:
            user_id = user.source_user_id

            # Get all folders for this user (respects filter settings)
            folder_names, folder_filter_mode = self._determine_folder_filter_strategy()
            folders, top_level_folder_ids = await self._get_all_folders_for_user(
                user_id,
                folder_names=folder_names,
                folder_filter_mode=folder_filter_mode
            )

            if not folders:
                self.logger.debug(f"No folders to sync for user {user.email}")
                return []

            # Transform folders to RecordGroups
            record_groups = []
            for folder in folders:
                is_top_level = folder.id in top_level_folder_ids
                record_group = self._transform_folder_to_record_group(folder, user, is_top_level)
                if record_group:
                    record_groups.append(record_group)

            self.logger.info(f"Syncing {len(record_groups)} folders for user {user.email}")

            # Sync to database with owner permission for mailbox owner
            if record_groups:
                # Create owner permission for the mailbox owner
                owner_permission = Permission(
                    email=user.email,
                    type=PermissionType.OWNER,
                    entity_type=EntityType.USER
                )

                # Apply owner permission to all folders for this user
                record_groups_with_permissions = [
                    (rg, [owner_permission]) for rg in record_groups
                ]

                await self.data_entities_processor.on_new_record_groups(record_groups_with_permissions)

            # Return raw folder data for email processing
            return folders

        except Exception as e:
            self.logger.error(f"Error syncing folders for user {user.email}: {e}")
            return []

    async def _process_single_folder_messages(self, org_id: str, user: AppUser, folder: MailFolder) -> tuple[int, list[Record]]:
        """Process messages using batch processing with automatic pagination."""
        try:
            user_id = user.source_user_id
            # folder is a MailFolder Pydantic object
            folder_id = folder.id
            folder_name = folder.display_name or OutlookDefaults.FOLDER_NAME

            # Create folder-specific sync point
            sync_point_key = generate_record_sync_point_key(
                RecordType.MAIL.value, OutlookSyncPointKeys.SEGMENT_FOLDERS, f"{user_id}_{folder_id}"
            )
            sync_point = await self.email_delta_sync_point.read_sync_point(sync_point_key)
            delta_link = sync_point.get(OutlookSyncPointKeys.DELTA_LINK) if sync_point else None

            # Get messages for this folder using delta sync
            result = await self._get_all_messages_delta_external(user_id, folder_id, delta_link)
            messages = result.messages

            self.logger.info(f"Retrieved {len(messages)} total message changes from folder '{folder_name}' for user {user.email}")

            if not messages:
                self.logger.info(f"No messages to process in folder '{folder_name}'")
                return 0, []

            # Collect all updates first for thread processing
            all_updates = []
            processed_count = 0
            mail_records = []  # Collect mail records for thread processing

            for message in messages:
                record_updates = await self._process_single_message(org_id, user, message, folder_id, folder_name)
                all_updates.extend(record_updates)

            # Process records in batches
            batch_records = []
            batch_size = BatchConfig.DEFAULT_BATCH_SIZE

            for update in all_updates:
                if update and update.record:
                    permissions = update.new_permissions or []
                    batch_records.append((update.record, permissions))

                    # Collect mail records (not attachments) for thread processing
                    if hasattr(update.record, 'record_type') and update.record.record_type == RecordType.MAIL:
                        mail_records.append(update.record)

                if len(batch_records) >= batch_size:
                    await self.data_entities_processor.on_new_records(batch_records)
                    processed_count += len(batch_records)
                    batch_records = []

            # Process remaining records
            if batch_records:
                await self.data_entities_processor.on_new_records(batch_records)
                processed_count += len(batch_records)

            # Update folder-specific sync point only if all batches were processed successfully
            sync_point_data = {
                OutlookSyncPointKeys.DELTA_LINK: result.delta_link,
                OutlookSyncPointKeys.LAST_SYNC_TIMESTAMP: get_epoch_timestamp_in_ms(),
                OutlookSyncPointKeys.FOLDER_ID: folder_id,
                OutlookSyncPointKeys.FOLDER_NAME: folder_name,
            }

            await self.email_delta_sync_point.update_sync_point(
                sync_point_key,
                sync_point_data,
                encrypt_fields=[OutlookSyncPointKeys.ENCRYPT_FIELD_DELTA_LINK]
            )

            # Log final summary
            self.logger.info(f"Folder '{folder_name}' completed: {processed_count} records processed from {len(messages)} messages")

            return processed_count, mail_records

        except Exception as e:
            self.logger.error(f"Error processing messages in folder '{folder_name}' for user {user.email}: {e}")
            return 0, []

    async def _get_all_messages_delta_external(self, user_id: str, folder_id: str, delta_link: str | None = None) -> MessagesDeltaResult:
        """Get folder messages using delta sync with automatic pagination from external Outlook API.

        This method handles both initial sync and incremental sync:
        - Initial sync (delta_link=None): Retrieves all messages in the folder
        - Incremental sync (delta_link provided): Retrieves only changes since last sync

        Pagination is handled automatically:
        - The method follows nextLink URLs to fetch all pages
        - Returns when deltaLink is received (signals completion)
        - Maximum page size is 200 messages per request

        Args:
            user_id: User identifier
            folder_id: Mail folder identifier
            delta_link: Previously saved deltaLink for incremental sync (optional)

        Returns:
            MessagesDeltaResult with:
                - messages: list[Message] - Pydantic Message objects
                - delta_link: str | None - New deltaLink to save for next sync
        """
        try:
            if not self.external_outlook_client:
                raise Exception("External Outlook client not initialized")

            # Build filter string for receivedDateTime if configured
            # Note: MS Graph delta queries have limited filter support
            # receivedDateTime filter only supports 'ge' (greater than or equal)
            # For 'le' (IS_BEFORE), we apply client-side filtering after fetching
            filter_string = None
            received_before_dt: datetime | None = None  # For client-side filtering

            received_date_filter = self.sync_filters.get(SyncFilterKey.RECEIVED_DATE)
            if received_date_filter and not received_date_filter.is_empty():
                received_after_iso, received_before_iso = received_date_filter.get_datetime_iso()

                # API supports 'ge' (greater than or equal) - apply server-side
                if received_after_iso:
                    filter_string = f"{OutlookODataFields.RECEIVED_DATE_TIME} ge {received_after_iso}Z"
                    self.logger.info(f"Applying received date filter (server-side): {filter_string}")

                # API doesn't support 'le' - we'll filter client-side
                if received_before_iso:
                    # Parse ISO string to datetime for client-side comparison
                    received_before_dt = datetime.strptime(received_before_iso, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                    self.logger.info(f"Will apply received date filter (client-side): receivedDateTime before {received_before_iso}")

            # Use the new fetch_all_messages_delta method that handles pagination automatically
            messages, new_delta_link = await self.external_outlook_client.fetch_all_messages_delta(
                user_id=user_id,
                mailFolder_id=folder_id,
                saved_delta_link=delta_link,
                page_size=OutlookSyncConfig.MESSAGE_PAGE_SIZE,
                filter=filter_string,
                select=OutlookAPIFields.MESSAGE_SELECT_FIELDS,
            )

            # Apply client-side filtering for IS_BEFORE if needed
            if received_before_dt is not None and messages:
                original_count = len(messages)
                filtered_messages = []

                # messages are Pydantic Message objects from fetch_all_messages_delta
                for msg in messages:
                    # Get receivedDateTime from message
                    received_dt = msg.received_date_time
                    if received_dt is None:
                        # If no received date, include the message
                        filtered_messages.append(msg)
                        continue

                    # Compare datetime objects directly
                    if isinstance(received_dt, datetime):
                        # Ensure timezone-aware comparison
                        if received_dt.tzinfo is None:
                            received_dt = received_dt.replace(tzinfo=timezone.utc)
                        # Include message if received before the cutoff
                        if received_dt < received_before_dt:
                            filtered_messages.append(msg)
                    else:
                        filtered_messages.append(msg)

                messages = filtered_messages
                filtered_out = original_count - len(messages)
                if filtered_out > 0:
                    self.logger.info(
                        f"Client-side date filter applied: {original_count} -> {len(messages)} messages "
                        f"(filtered out {filtered_out} messages received after cutoff)"
                    )

            self.logger.info(f"Delta sync completed for folder {folder_id}: retrieved {len(messages)} total messages across all pages")

            return MessagesDeltaResult(
                messages=messages,
                delta_link=new_delta_link
            )

        except Exception as e:
            self.logger.error(f"Error getting messages delta for folder {folder_id}: {e}", exc_info=True)
            return MessagesDeltaResult(
                messages=[],
                delta_link=None
            )

    async def _process_single_message(
        self, org_id: str, user: AppUser, message: Message, folder_id: str, folder_name: str
    ) -> list[RecordUpdate]:
        """Process one message and its attachments together.

        Args:
            message: Pydantic Message object
        """
        updates = []

        try:
            # message is a Pydantic Message object
            message_id = message.id

            # Check if message is deleted
            additional_data = message.additional_data or {}
            is_deleted = (additional_data.get('@removed', {}).get('reason') == 'deleted')

            if is_deleted:
                self.logger.info(f"Deleting message: {message_id} and its attachments from folder {folder_name}")
                async with self.data_store_provider.transaction() as tx_store:
                    await tx_store.delete_record_by_external_id(self.connector_id, message_id, user.user_id)
                return updates

            # Process email with attachments
            email_update = await self._process_single_email_with_folder(org_id, user.email, message, folder_id, folder_name)
            if email_update:
                updates.append(email_update)

                # Process attachments if any
                has_attachments = message.has_attachments or False
                if has_attachments:
                    email_permissions = await self._extract_email_permissions(message, None, user.email)
                    attachment_updates = await self._process_email_attachments_with_folder(
                        org_id, user, message, email_permissions, folder_id, folder_name
                    )
                    if attachment_updates:
                        updates.extend(attachment_updates)
            else:
                self.logger.debug(f"Skipping attachment processing for unchanged email {message_id}")

        except Exception as e:
            self.logger.error(f"Error processing message {message.id if hasattr(message, 'id') else 'unknown'}: {e}")

        return updates

    async def _process_single_email_with_folder(
        self,
        org_id: str,
        user_email: str,
        message: Message,
        folder_id: str,
        folder_name: str,
        existing_record: Record | None = None,
    ) -> RecordUpdate | None:
        """Process a single email with folder information.

        Args:
            message: Pydantic Message object
            existing_record: Optional existing record to skip DB lookup (used during reindex)
        """
        try:
            # message is a Pydantic Message object
            message_id = message.id

            # Skip DB lookup if existing_record is provided (reindex case)
            if existing_record is None:
                existing_record = await self._get_existing_record(org_id, message_id)
            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False

            if not is_new:
                current_etag = message.change_key
                if existing_record.external_revision_id != current_etag:
                    content_changed = True
                    is_updated = True
                    self.logger.info(f"Email {message_id} content changed (change_key: {existing_record.external_revision_id} -> {current_etag})")

                current_folder_id = folder_id
                existing_folder_id = existing_record.external_record_group_id
                if existing_folder_id and current_folder_id != existing_folder_id:
                    metadata_changed = True
                    is_updated = True
                    self.logger.info(f"Email {message_id} moved from folder {existing_folder_id} to {current_folder_id}")

            record_id = existing_record.id if existing_record else str(uuid.uuid4())

            # Create email record with folder information
            email_record = MailRecord(
                id=record_id,
                org_id=org_id,
                record_name=message.subject or OutlookDefaults.SUBJECT,
                record_type=RecordType.MAIL,
                external_record_id=message_id,
                external_revision_id=message.change_key,
                version=0 if is_new else existing_record.version + 1,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.OUTLOOK,
                connector_id=self.connector_id,
                source_created_at=datetime_to_epoch_ms(message.created_date_time),
                source_updated_at=datetime_to_epoch_ms(message.last_modified_date_time),
                weburl=message.web_link or '',
                mime_type=MimeTypes.HTML.value,
                parent_external_record_id=None,
                external_record_group_id=folder_id,
                record_group_type=RecordGroupType.MAILBOX,
                subject=message.subject or OutlookDefaults.SUBJECT,
                from_email=self._extract_email_from_recipient(message.from_),
                to_emails=[self._extract_email_from_recipient(r) for r in (message.to_recipients or [])],
                cc_emails=[self._extract_email_from_recipient(r) for r in (message.cc_recipients or [])],
                bcc_emails=[self._extract_email_from_recipient(r) for r in (message.bcc_recipients or [])],
                thread_id=message.conversation_id or '',
                is_parent=False,
                internet_message_id=message.internet_message_id or '',
                conversation_index=message.conversation_index or '',
            )

            # Apply indexing filter for mail records
            if not self.indexing_filters.is_enabled(IndexingFilterKey.MAILS, default=True):
                email_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

            permissions = await self._extract_email_permissions(message, email_record.id, user_email)

            return RecordUpdate(
                record=email_record,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=False,
                metadata_changed=metadata_changed,
                content_changed=content_changed,
                permissions_changed=bool(permissions),
                new_permissions=permissions,
                external_record_id=message_id,
            )

        except Exception as e:
            self.logger.error(f"Error processing email {message.id if hasattr(message, 'id') else 'unknown'}: {str(e)}")
            return None

    async def _extract_email_permissions(self, message: Message, record_id: str | None, inbox_owner_email: str) -> list[Permission]:
        """Extract permissions from email recipients.

        Args:
            message: Pydantic Message object

        Note: This method is for PERSONAL mailbox emails only.
        """
        permissions = []

        try:
            # message is a Pydantic Message object
            # Process all recipients (existing logic)
            all_recipients = []
            all_recipients.extend(message.to_recipients or [])
            all_recipients.extend(message.cc_recipients or [])
            all_recipients.extend(message.bcc_recipients or [])

            # Add sender
            from_recipient = message.from_
            if from_recipient:
                all_recipients.append(from_recipient)

            # Track unique emails
            processed_emails = set()
            inbox_owner_email_lower = inbox_owner_email.lower()
            owner_found = False

            # Process individual recipients
            for recipient in all_recipients:
                try:
                    email_address = self._extract_email_from_recipient(recipient)
                    if email_address and email_address not in processed_emails:
                        processed_emails.add(email_address)

                        # Inbox owner always gets OWNER permission, others get READ
                        if email_address.lower() == inbox_owner_email_lower:
                            permission_type = PermissionType.OWNER
                            owner_found = True
                        else:
                            permission_type = PermissionType.READ

                        permission = Permission(
                            email=email_address,
                            type=permission_type,
                            entity_type=EntityType.USER,
                        )
                        permissions.append(permission)

                except Exception as e:
                    self.logger.warning(f"Failed to extract email from recipient {recipient}: {e}")
                    continue

            # If inbox owner not found in recipients, add OWNER permission
            if not owner_found and inbox_owner_email:
                owner_permission = Permission(
                    email=inbox_owner_email,
                    type=PermissionType.OWNER,
                    entity_type=EntityType.USER,
                )
                permissions.append(owner_permission)

            return permissions

        except Exception as e:
            self.logger.error(f"Error extracting permissions: {e}")
            return []

    async def _create_attachment_record(
        self,
        org_id: str,
        attachment: Attachment,
        message_id: str,
        folder_id: str,
        existing_record: Record | None = None,
        parent_weburl: str | None = None,
    ) -> FileRecord | None:
        """Helper method to create a FileRecord from an attachment.

        Args:
            org_id: Organization ID
            attachment: Pydantic Attachment object
            message_id: Parent message ID
            folder_id: Folder ID
            existing_record: Existing record if updating
            parent_weburl: Web URL of the parent mail

        Returns:
            FileRecord: Created attachment record, or None if attachment should be skipped
        """
        # attachment is a Pydantic Attachment object
        attachment_id = attachment.id
        is_new = existing_record is None

        # Check if content_type is available, skip attachment if not
        content_type = attachment.content_type
        if not content_type:
            file_name = attachment.name or OutlookDefaults.UNKNOWN_FOLDER_LABEL
            self.logger.warning(f"Skipping attachment '{file_name}' (id: {attachment_id}) - no content_type available")
            return None

        mime_type = self._get_mime_type_enum(content_type)

        file_name = attachment.name or OutlookDefaults.ATTACHMENT_NAME
        extension = None
        if '.' in file_name:
            extension = file_name.split('.')[-1].lower()

        attachment_record_id = existing_record.id if existing_record else str(uuid.uuid4())

        if not parent_weburl:
            self.logger.error(f"No parent weburl found for attachment id {attachment_id}, file name {file_name}, with parent message id {message_id}")

        attachment_record = FileRecord(
            id=attachment_record_id,
            org_id=org_id,
            record_name=file_name,
            record_type=RecordType.FILE,
            external_record_id=attachment_id,
            external_revision_id=attachment.last_modified_date_time.isoformat() if attachment.last_modified_date_time else None,
            version=0 if is_new else existing_record.version + 1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.OUTLOOK,
            connector_id=self.connector_id,
            source_created_at=datetime_to_epoch_ms(attachment.last_modified_date_time),
            source_updated_at=datetime_to_epoch_ms(attachment.last_modified_date_time),
            mime_type=mime_type,
            parent_external_record_id=message_id,
            parent_record_type=RecordType.MAIL,
            external_record_group_id=folder_id,
            record_group_type=RecordGroupType.MAILBOX,
            weburl=parent_weburl,
            is_file=True,
            size_in_bytes=attachment.size or 0,
            extension=extension,
        )

        # Apply indexing filter for attachment records
        if not self.indexing_filters.is_enabled(IndexingFilterKey.ATTACHMENTS, default=True):
            attachment_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

        return attachment_record

    async def _process_email_attachments_with_folder(self, org_id: str, user: AppUser, message: Message,
                                                  email_permissions: list[Permission], folder_id: str, folder_name: str) -> list[RecordUpdate]:
        """Process email attachments with folder information.

        Args:
            message: Pydantic Message object
        """
        attachment_updates = []

        try:
            user_id = user.source_user_id
            # message is a Pydantic Message object
            message_id = message.id
            parent_weburl = message.web_link

            # attachments are Pydantic Attachment objects from _get_message_attachments_external
            attachments = await self._get_message_attachments_external(user_id, message_id)

            for attachment in attachments:
                attachment_id = attachment.id
                existing_record = await self._get_existing_record(org_id, attachment_id)
                is_new = existing_record is None
                is_updated = False
                metadata_changed = False
                content_changed = False

                if not is_new:
                    current_revision = attachment.last_modified_date_time.isoformat() if attachment.last_modified_date_time else None
                    if existing_record.external_revision_id != current_revision:
                        content_changed = True
                        is_updated = True
                        self.logger.info(f"Attachment {attachment_id} content changed (revision changed)")

                    current_folder_id = folder_id
                    existing_folder_id = existing_record.external_record_group_id
                    if existing_folder_id and current_folder_id != existing_folder_id:
                        metadata_changed = True
                        is_updated = True

                attachment_record = await self._create_attachment_record(
                    org_id, attachment, message_id, folder_id, existing_record, parent_weburl
                )

                # Skip if attachment was filtered out (e.g., no content_type)
                if not attachment_record:
                    continue

                attachment_updates.append(RecordUpdate(
                    record=attachment_record,
                    is_new=is_new,
                    is_updated=is_updated,
                    is_deleted=False,
                    metadata_changed=metadata_changed,
                    content_changed=content_changed,
                    permissions_changed=bool(email_permissions),
                    new_permissions=email_permissions,
                    external_record_id=attachment_id,
                ))

            return attachment_updates

        except Exception as e:
            self.logger.error(f"Error processing attachments for email {message.id if hasattr(message, 'id') else 'unknown'}: {e}")
            return []

    async def _get_message_attachments_external(self, user_id: str, message_id: str) -> list[Attachment]:
        """Get message attachments using external Outlook API.

        Returns:
            List of Pydantic Attachment objects
        """
        try:
            if not self.external_outlook_client:
                raise Exception("External Outlook client not initialized")

            response: OutlookCalendarContactsResponse = await self.external_outlook_client.users_messages_list_attachments(
                user_id=user_id,
                message_id=message_id
            )


            if not response.success:
                self.logger.error(f"Failed to get attachments for message {message_id}: {response.error}")
                return []

            # response.data is AttachmentCollectionResponse with .value containing list[Attachment]
            return response.data.value if response.data.value else []

        except Exception as e:
            self.logger.error(f"Error getting attachments for message {message_id}: {e}")
            return []

    async def _get_existing_record(self, org_id: str, external_record_id: str) -> Record | None:
        """Get existing record from data store."""
        try:
            async with self.data_store_provider.transaction() as tx_store:
                return await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=external_record_id
                )
        except Exception as e:
            self.logger.error(f"Error getting existing record {external_record_id}: {e}")
            return None

    def _augment_email_html_with_metadata(self, email_body: str, record: MailRecord) -> str:
        """Augment email HTML with searchable recipient metadata.

        Prepends a hidden div containing email metadata (from, to, cc, bcc, subject)
        to the HTML content. This makes recipient information searchable while keeping
        the original email HTML intact and visually unaffected.

        Args:
            email_body: Original HTML content from email body
            record: MailRecord containing metadata (from, to, cc, bcc, subject)

        Returns:
            HTML string with prepended metadata div
        """
        metadata_parts = {
            "From": record.from_email,
            "To": ", ".join(record.to_emails) if record.to_emails else None,
            "CC": ", ".join(record.cc_emails) if record.cc_emails else None,
            "BCC": ", ".join(record.bcc_emails) if record.bcc_emails else None,
            "Subject": record.subject,
        }

        metadata_lines = [
            f"{key}: {html.escape(value)}"
            for key, value in metadata_parts.items()
            if value
        ]

        if metadata_lines:
            metadata_content = "<br>\n".join(metadata_lines)
            metadata_div = f'<div style="display:none;" class="email-metadata">{metadata_content}</div>\n'
            return metadata_div + email_body

        return email_body

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Stream record content (email or attachment)."""
        try:
            if not self.external_outlook_client:
                raise HTTPException(
                    status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                    detail=OutlookHTTPDetails.CLIENT_NOT_INITIALIZED,
                )

            # Handle group posts (don't need user_id)
            if record.record_type == RecordType.GROUP_MAIL:
                group_id = record.external_record_group_id
                thread_id = record.thread_id if hasattr(record, 'thread_id') else None
                post_id = record.external_record_id

                if not group_id:
                    raise HTTPException(
                        status_code=HttpStatusCode.BAD_REQUEST.value,
                        detail=OutlookHTTPDetails.MISSING_GROUP_ID_POST,
                    )

                if not thread_id:
                    raise HTTPException(
                        status_code=HttpStatusCode.BAD_REQUEST.value,
                        detail=OutlookHTTPDetails.MISSING_THREAD_ID_POST,
                    )

                response = await self.external_outlook_client.groups_threads_get_post(
                    group_id=group_id,
                    thread_id=thread_id,
                    post_id=post_id
                )

                if not response.success:
                    raise HTTPException(
                        status_code=HttpStatusCode.NOT_FOUND.value,
                        detail=f"Post not found: {response.error}",
                    )

                # response.data is a Pydantic Post object
                post = response.data

                # Extract body content from Pydantic Post object
                post_body = post.body.content or '' if post and post.body else ''

                # Augment with metadata for indexing
                if isinstance(record, MailRecord):
                    post_body = self._augment_email_html_with_metadata(post_body, record)
                async def generate_post() -> AsyncGenerator[bytes, None]:
                    yield post_body.encode('utf-8')

                return StreamingResponse(generate_post(), media_type=OutlookMediaTypes.TEXT_HTML)

            # Handle FILE records (check if parent is group post)
            if record.record_type == RecordType.FILE and record.parent_external_record_id:
                # Get parent record to check its type
                async with self.data_store_provider.transaction() as tx_store:
                    parent_record = await tx_store.get_record_by_external_id(
                        connector_id=self.connector_id,
                        external_id=record.parent_external_record_id
                    )

                if parent_record and parent_record.record_type == RecordType.GROUP_MAIL:
                    # Group post attachment (don't need user_id)
                    group_id = record.external_record_group_id or parent_record.external_record_group_id
                    post_id = record.parent_external_record_id
                    attachment_id = record.external_record_id
                    thread_id = parent_record.thread_id

                    if not group_id or not thread_id:
                        raise HTTPException(
                            status_code=HttpStatusCode.BAD_REQUEST.value,
                            detail=OutlookHTTPDetails.MISSING_GROUP_OR_THREAD_FOR_ATTACHMENT,
                        )

                    attachment_data = await self._download_group_post_attachment(
                        group_id, thread_id, post_id, attachment_id
                    )

                    async def generate_group_attachment() -> AsyncGenerator[bytes, None]:
                        yield attachment_data

                    return create_stream_record_response(
                        generate_group_attachment(),
                        filename=record.record_name or "attachment",
                        mime_type=record.mime_type,
                        fallback_filename=f"record_{record.id}"
                    )

            # User mailbox records (need user_id)
            user_id = None

            async with self.data_store_provider.transaction() as tx_store:
                user_email = await tx_store.get_record_owner_source_user_email(record.id)
                if user_email:
                    user_id = await self._get_user_id_from_email(user_email)

            if not user_id:
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail=OutlookHTTPDetails.USER_CONTEXT_UNKNOWN,
                )

            if record.record_type == RecordType.MAIL:
                # User email - message is a Pydantic Message object
                message = await self._get_message_by_id_external(user_id, record.external_record_id)

                # Extract body content from Pydantic Message object
                if message and message.body:
                    email_body = message.body.content or ''
                else:
                    email_body = ''
                # Augment with recipient metadata for indexing
                if isinstance(record, MailRecord):
                    email_body = self._augment_email_html_with_metadata(email_body, record)
                async def generate_email() -> AsyncGenerator[bytes, None]:
                    yield email_body.encode('utf-8')

                return StreamingResponse(generate_email(), media_type=OutlookMediaTypes.TEXT_HTML)

            elif record.record_type == RecordType.FILE:
                # User email attachment
                attachment_id = record.external_record_id
                parent_message_id = record.parent_external_record_id

                if not parent_message_id:
                    raise HTTPException(
                        status_code=HttpStatusCode.NOT_FOUND.value,
                        detail=OutlookHTTPDetails.NO_PARENT_MESSAGE,
                    )

                attachment_data = await self._download_attachment_external(user_id, parent_message_id, attachment_id)

                async def generate_attachment() -> AsyncGenerator[bytes, None]:
                    yield attachment_data

                filename = record.record_name or "attachment"
                return create_stream_record_response(
                    generate_attachment(),
                    filename=filename,
                    mime_type=record.mime_type,
                    fallback_filename=f"record_{record.id}"
                )

            else:
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail=OutlookHTTPDetails.UNSUPPORTED_RECORD_TYPE,
                )

        except Exception as e:
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail=f"Failed to stream record: {str(e)}",
            ) from e

    async def _get_message_by_id_external(self, user_id: str, message_id: str) -> Message | None:
        """Get a specific message by ID using external Outlook API.

        Returns:
            Pydantic Message object or None
        """
        try:
            if not self.external_outlook_client:
                raise Exception("External Outlook client not initialized")

            response: OutlookCalendarContactsResponse = await self.external_outlook_client.users_get_messages(
                user_id=user_id,
                message_id=message_id
            )

            if not response.success:
                self.logger.error(f"Failed to get message {message_id}: {response.error}")
                return None

            return response.data

        except Exception as e:
            self.logger.error(f"Error getting message {message_id}: {e}")
            return None

    async def _download_attachment_external(self, user_id: str, message_id: str, attachment_id: str) -> bytes:
        """Download attachment content using external Outlook API."""
        try:
            if not self.external_outlook_client:
                raise Exception("External Outlook client not initialized")

            response: OutlookCalendarContactsResponse = await self.external_outlook_client.users_messages_get_attachments(
                user_id=user_id,
                message_id=message_id,
                attachment_id=attachment_id
            )

            if not response.success or not response.data:
                return b''

            # response.data is a Pydantic FileAttachment object
            attachment_data = response.data
            content_bytes = attachment_data.content_bytes if attachment_data else None

            if not content_bytes:
                return b''

            return base64.b64decode(content_bytes)

        except Exception as e:
            self.logger.error(f"Error downloading attachment {attachment_id} for message {message_id}: {e}")
            return b''


    def get_signed_url(self, record: Record) -> str | None:
        """Get signed URL for record access. Not supported for Outlook."""
        return None


    async def handle_webhook_notification(self, org_id: str, notification: dict) -> bool:
        """Handle webhook notifications from Microsoft Graph."""
        try:
            return True
        except Exception as e:
            self.logger.error(f"Error handling webhook notification: {e}")
            return False


    async def cleanup(self) -> None:
        """Clean up resources used by the connector."""
        try:
            # Close the MSGraph client to properly close the HTTP transport
            if hasattr(self, 'external_client') and self.external_client:
                try:
                    underlying_client = self.external_client.get_client()
                    if hasattr(underlying_client, 'close'):
                        await underlying_client.close()
                except Exception as client_error:
                    self.logger.debug(f"Error closing MSGraph client: {client_error}")
                finally:
                    self.external_client = None

            self.external_outlook_client = None
            self.external_users_client = None
            self.credentials = None
            # Clear user cache
            self._user_cache.clear()
            self._user_cache_timestamp = None
        except Exception as e:
            self.logger.error(f"Error during Outlook connector cleanup: {e}")


    async def run_incremental_sync(self) -> None:
        """Run incremental synchronization for Outlook emails."""
        # Delegate to full sync - incremental is handled by delta links
        await self.run_sync()

    async def reindex_records(self, records: list[Record]) -> None:
        """Reindex a list of Outlook records.

        This method:
        1. For each record, checks if it has been updated at the source
        2. If updated, upserts the record in DB
        3. Publishes reindex events for all records via data_entities_processor

        Args:
            records: List of properly typed Record instances (MailRecord, FileRecord, etc.)
        """
        try:
            if not records:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(records)} Outlook records")

            # Ensure external clients are initialized
            if not self.external_outlook_client or not self.external_users_client:
                self.logger.error("External API clients not initialized. Call init() first.")
                raise Exception("External API clients not initialized. Call init() first.")

            # Populate user cache for better performance
            await self._populate_user_cache()

            # Separate GROUP_MAIL records from user mailbox records
            user_mailbox_records = []
            group_mailbox_records = []

            for record in records:
                if record.record_type == RecordType.GROUP_MAIL:
                    group_mailbox_records.append(record)
                elif record.record_type == RecordType.FILE and record.parent_external_record_id:
                    # Check if it's a GROUP_MAIL attachment
                    async with self.data_store_provider.transaction() as tx_store:
                        parent_record = await tx_store.get_record_by_external_id(
                            connector_id=self.connector_id,
                            external_id=record.parent_external_record_id
                        )
                    if parent_record and parent_record.record_type == RecordType.GROUP_MAIL:
                        group_mailbox_records.append(record)
                    else:
                        user_mailbox_records.append(record)
                else:
                    user_mailbox_records.append(record)

            self.logger.info(f"Separated: {len(user_mailbox_records)} user mailbox records, {len(group_mailbox_records)} group mailbox records")

            # Process user mailbox records
            user_updated, user_non_updated = await self._reindex_user_mailbox_records(user_mailbox_records)

            # Process group mailbox records
            group_updated, group_non_updated = await self._reindex_group_mailbox_records(group_mailbox_records)

            # Combine results
            all_updated_records_with_permissions = user_updated + group_updated
            all_non_updated_records = user_non_updated + group_non_updated

            # Update DB and publish events for updated records
            if all_updated_records_with_permissions:
                await self.data_entities_processor.on_new_records(all_updated_records_with_permissions)
                self.logger.info(f"Updated {len(all_updated_records_with_permissions)} records in DB that changed at source")

            # Publish reindex events for non-updated records
            if all_non_updated_records:
                await self.data_entities_processor.reindex_existing_records(all_non_updated_records)
                self.logger.info(f"Published reindex events for {len(all_non_updated_records)} non-updated records")

            self.logger.info(f"Outlook reindex completed for {len(records)} records")

        except Exception as e:
            self.logger.error(f"Error during Outlook reindex: {e}")
            raise

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: str | None = None,
        cursor: str | None = None
    ) -> FilterOptionsResponse:
        """Get dynamic filter options for the users or groups filter."""
        if filter_key == SyncFilterKey.USERS.value:
            return await self._get_user_options(page, limit, search, cursor)
        if filter_key == SyncFilterKey.GROUPS.value:
            return await self._get_group_options(page, limit, search, cursor)
        raise ValueError(f"Unsupported filter key: {filter_key}")

    def _graph_user_to_filter_option(self, user: object) -> FilterOption | None:
        """Build a FilterOption from a Microsoft Graph Pydantic User object."""
        # user is a Pydantic User object
        email = user.mail or user.user_principal_name
        if not email:
            return None

        display_name = user.display_name or ""
        given_name = user.given_name or ""
        surname = user.surname or ""

        full_name = display_name if display_name else f"{given_name} {surname}".strip()
        if not full_name:
            full_name = email
        display_label = f"{full_name} ({email})" if full_name else email
        return FilterOption(id=email, label=display_label)

    async def _get_user_options(
        self,
        page: int,
        limit: int,
        search: str | None,
        cursor: str | None = None,
    ) -> FilterOptionsResponse:
        """List users for the filter UI with one Graph request per call.

        Without a search term: uses ``$top`` / ``$skip`` for offset pagination.
        With a search term: uses ``$search`` with OR clauses on displayName, mail, and
        userPrincipalName (``$count=true`` + ConsistencyLevel are set by the client).
        Pagination uses ``@odata.nextLink`` only.
        """
        try:
            if not self.external_users_client:
                return FilterOptionsResponse(
                    success=False,
                    options=[],
                    page=page,
                    limit=limit,
                    has_more=False,
                    message="Outlook connector is not initialized",
                )

            cap = max(1, min(limit, OutlookSyncConfig.MAX_FOLDER_PAGE_SIZE))
            search_term = search.strip() if search else None

            if search_term:
                if page > 1 and not cursor:
                    return FilterOptionsResponse(
                        success=True,
                        options=[],
                        page=page,
                        limit=cap,
                        has_more=False,
                        message="Paginated search requires the cursor from the previous response.",
                    )
                if cursor:
                    response: UsersGroupsResponse = await self.external_users_client.users_user_list_user(
                        next_url=cursor,
                    )
                else:
                    esc = search_term.replace("\\", "\\\\").replace('"', '\\"')
                    graph_search = (f'"displayName:{esc}" OR "mail:{esc}" OR "userPrincipalName:{esc}"')
                    response = await self.external_users_client.users_user_list_user(
                        top=cap,
                        search=graph_search,
                        select=OutlookAPIFields.USER_FILTER_SELECT_FIELDS,
                        headers={"ConsistencyLevel": "eventual"},
                    )
            else:
                skip = (page - 1) * cap
                response = await self.external_users_client.users_user_list_user(
                    top=cap,
                    skip=skip,
                    select=OutlookAPIFields.USER_FILTER_SELECT_FIELDS,
                    orderby="displayName",
                )

            if not response.success or not response.data:
                return FilterOptionsResponse(
                    success=False,
                    options=[],
                    page=page,
                    limit=cap,
                    has_more=False,
                    message=response.error or "Failed to fetch users",
                )

            # response.data is UserCollectionResponse with .value containing list[User]
            user_data = response.data.value if response.data.value else []

            user_options: list[FilterOption] = []
            for user in user_data:
                opt = self._graph_user_to_filter_option(user)
                if opt:
                    user_options.append(opt)

            # Get next URL from response
            next_url = response.data.odata_next_link if response.data.odata_next_link else None

            has_more = bool(next_url)
            out_cursor = str(next_url) if next_url else None

            return FilterOptionsResponse(
                success=True,
                options=user_options,
                page=page,
                limit=cap,
                has_more=has_more,
                cursor=out_cursor,
            )

        except Exception as e:
            self.logger.error(f"Failed to get user options: {e}")
            return FilterOptionsResponse(
                success=False,
                options=[],
                page=page,
                limit=limit,
                has_more=False,
                message=f"Error fetching users: {str(e)}",
            )

    def _graph_group_to_filter_option(self, group: object) -> FilterOption | None:
        """Build a FilterOption from a Microsoft Graph Pydantic Group object.

        Only includes mail-enabled groups (distribution lists,
        mail-enabled security groups, Microsoft 365 groups, etc.).
        Returns None if the group is not mail-enabled or has no mail.
        """
        if not group.mail_enabled:
            return None

        mail = group.mail
        if not mail:
            return None

        display_name = group.display_name or ""
        mail_nickname = group.mail_nickname or ""

        label_name = display_name or mail_nickname or mail
        display_label = f"{label_name} ({mail})"
        return FilterOption(id=mail, label=display_label)

    async def _get_group_options(
        self,
        page: int,
        limit: int,
        search: str | None,
        cursor: str | None = None,
    ) -> FilterOptionsResponse:
        """List Microsoft 365 groups for the filter UI with one Graph request per call.

        Graph ``GET /groups`` does not support ``$skip``; listing uses ``$top`` and
        ``@odata.nextLink`` only (page > 1 requires ``cursor`` from the prior response).
        With a search term: ``$search`` on displayName and mail (count + ConsistencyLevel
        are set by the client). Search pagination is also nextLink-only.
        """
        try:
            if not self.external_users_client:
                return FilterOptionsResponse(
                    success=False,
                    options=[],
                    page=page,
                    limit=limit,
                    has_more=False,
                    message="Outlook connector is not initialized",
                )

            cap = max(1, min(limit, OutlookSyncConfig.MAX_FOLDER_PAGE_SIZE))
            search_term = search.strip() if search else None

            if search_term:
                if page > 1 and not cursor:
                    return FilterOptionsResponse(
                        success=True,
                        options=[],
                        page=page,
                        limit=cap,
                        has_more=False,
                        message="Paginated search requires the cursor from the previous response.",
                    )
                if cursor:
                    response: UsersGroupsResponse = await self.external_users_client.groups_list_groups(
                        next_url=cursor,
                    )
                else:
                    esc = search_term.replace("\\", "\\\\").replace('"', '\\"')
                    graph_search = f'"displayName:{esc}" OR "mail:{esc}"'
                    response = await self.external_users_client.groups_list_groups(
                        top=cap,
                        search=graph_search,
                        filter="mailEnabled eq true",
                        select=OutlookAPIFields.GROUP_FILTER_SELECT_FIELDS,
                        headers={"ConsistencyLevel": "eventual"},
                    )
            else:
                if page > 1 and not cursor:
                    return FilterOptionsResponse(
                        success=True,
                        options=[],
                        page=page,
                        limit=cap,
                        has_more=False,
                        message="Paginated listing requires the cursor from the previous response.",
                    )
                if cursor:
                    response = await self.external_users_client.groups_list_groups(
                        next_url=cursor,
                    )
                else:
                    response = await self.external_users_client.groups_list_groups(
                        top=cap,
                        filter="mailEnabled eq true",
                        select=OutlookAPIFields.GROUP_FILTER_SELECT_FIELDS,
                        orderby="displayName",
                    )

            if not response.success or not response.data:
                return FilterOptionsResponse(
                    success=False,
                    options=[],
                    page=page,
                    limit=cap,
                    has_more=False,
                    message=response.error or "Failed to fetch groups",
                )

            # response.data is GroupCollectionResponse with .value containing list[Group]
            group_data = response.data.value if response.data.value else []

            group_options: list[FilterOption] = []
            for group in group_data:
                opt = self._graph_group_to_filter_option(group)
                if opt:
                    group_options.append(opt)

            # Get next URL from response
            next_url = response.data.odata_next_link if response.data.odata_next_link else None

            has_more = bool(next_url)
            out_cursor = str(next_url) if next_url else None

            return FilterOptionsResponse(
                success=True,
                options=group_options,
                page=page,
                limit=cap,
                has_more=has_more,
                cursor=out_cursor,
            )

        except Exception as e:
            self.logger.error("Failed to get group options: %s", e, exc_info=True)
            return FilterOptionsResponse(
                success=False,
                options=[],
                page=page,
                limit=limit,
                has_more=False,
                message=f"Error fetching groups: {str(e)}",
            )

    async def _reindex_user_mailbox_records(
        self, records: list[Record]
    ) -> tuple[list[tuple[Record, list[Permission]]], list[Record]]:
        """Reindex user mailbox records. Checks source for updates.

        Returns:
            Tuple of (updated_records_with_permissions, non_updated_records)
        """
        if not records:
            return ([], [])

        # Group records by owner email for efficient processing
        records_by_user: dict[str, list[Record]] = {}
        for record in records:
            try:
                # Get owner email from permissions
                async with self.data_store_provider.transaction() as tx_store:
                    user_email = await tx_store.get_record_owner_source_user_email(record.id)

                if not user_email:
                    self.logger.warning(f"No owner found for record {record.id}, skipping")
                    continue

                if user_email not in records_by_user:
                    records_by_user[user_email] = []
                records_by_user[user_email].append(record)
            except Exception as e:
                self.logger.error(f"Error getting owner for record {record.id}: {e}")
                continue

        # Collect updated and non-updated records across all users
        all_updated_records_with_permissions: list[tuple[Record, list[Permission]]] = []
        all_non_updated_records: list[Record] = []

        # Process records by user - check for source updates
        for user_email, user_records in records_by_user.items():
            try:
                updated, non_updated = await self._reindex_single_user_records(user_email, user_records)
                all_updated_records_with_permissions.extend(updated)
                all_non_updated_records.extend(non_updated)
            except Exception as e:
                self.logger.error(f"Error reindexing records for user {user_email}: {e}")

        return (all_updated_records_with_permissions, all_non_updated_records)

    async def _reindex_single_user_records(
        self, user_email: str, records: list[Record]
    ) -> tuple[list[tuple[Record, list[Permission]]], list[Record]]:
        """Reindex records for a specific user. Checks source for updates.

        Returns:
            Tuple of (updated_records_with_permissions, non_updated_records)
        """
        updated_records_with_permissions: list[tuple[Record, list[Permission]]] = []
        non_updated_records: list[Record] = []

        try:
            user_id = await self._get_user_id_from_email(user_email)
            if not user_id:
                self.logger.error(f"Could not find user ID for email {user_email}")
                return ([], records)  # Return all as non-updated if user not found

            self.logger.info(f"Checking {len(records)} records at source for user {user_email}")

            org_id = self.data_entities_processor.org_id

            for record in records:
                try:
                    updated_record_data = await self._check_and_fetch_updated_record(
                        org_id, user_id, user_email, record
                    )
                    if updated_record_data:
                        updated_record, permissions = updated_record_data
                        updated_records_with_permissions.append((updated_record, permissions))
                    else:
                        non_updated_records.append(record)
                except Exception as e:
                    self.logger.error(f"Error checking record {record.id} at source: {e}")
                    continue

            self.logger.info(f"Completed source check for user {user_email}: {len(updated_records_with_permissions)} updated, {len(non_updated_records)} unchanged")

        except Exception as e:
            self.logger.error(f"Error reindexing records for user {user_email}: {e}")
            raise

        return (updated_records_with_permissions, non_updated_records)

    async def _reindex_group_mailbox_records(
        self, records: list[Record]
    ) -> tuple[list[tuple[Record, list[Permission]]], list[Record]]:
        """Reindex GROUP_MAIL records (no user_id needed).

        Returns:
            Tuple of (updated_records_with_permissions, non_updated_records)
        """
        updated_records_with_permissions: list[tuple[Record, list[Permission]]] = []
        non_updated_records: list[Record] = []

        if not records:
            return ([], [])

        try:
            org_id = self.data_entities_processor.org_id

            self.logger.info(f"Checking {len(records)} GROUP_MAIL records at source")

            for record in records:
                try:
                    updated_record_data = await self._check_and_fetch_updated_group_mail_record(org_id, record)
                    if updated_record_data:
                        updated_record, permissions = updated_record_data
                        updated_records_with_permissions.append((updated_record, permissions))
                    else:
                        non_updated_records.append(record)
                except Exception as e:
                    self.logger.error(f"Error checking GROUP_MAIL record {record.id} at source: {e}")
                    continue

            self.logger.info(f"Completed GROUP_MAIL source check: {len(updated_records_with_permissions)} updated, {len(non_updated_records)} unchanged")

        except Exception as e:
            self.logger.error(f"Error reindexing GROUP_MAIL records: {e}")
            raise

        return (updated_records_with_permissions, non_updated_records)

    async def _check_and_fetch_updated_group_mail_record(
        self, org_id: str, record: Record
    ) -> tuple[Record, list[Permission]] | None:
        """Fetch GROUP_MAIL record from source (mirrors stream_record logic).

        Args:
            org_id: Organization ID
            record: GROUP_MAIL or FILE (with GROUP_MAIL parent) record

        Returns:
            Tuple of (Record, List[Permission]) if updated, None otherwise
        """
        try:
            # Handle GROUP_MAIL posts
            if record.record_type == RecordType.GROUP_MAIL:
                return await self._check_and_fetch_updated_group_post(org_id, record)

            # Handle GROUP_MAIL attachments
            elif record.record_type == RecordType.FILE:
                return await self._check_and_fetch_updated_group_post_attachment(org_id, record)

            else:
                self.logger.warning(f"Unexpected record type in GROUP_MAIL reindex: {record.record_type}")
                return None

        except Exception as e:
            self.logger.error(f"Error checking GROUP_MAIL record {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_group_post(
        self, org_id: str, record: MailRecord
    ) -> tuple[Record, list[Permission]] | None:
        """Fetch group post from source and check for updates."""
        try:
            group_id = record.external_record_group_id
            thread_id = record.thread_id if hasattr(record, 'thread_id') else None
            post_id = record.external_record_id

            if not group_id:
                self.logger.warning(f"GROUP_MAIL record {record.id} missing group_id")
                return None

            if not thread_id:
                self.logger.warning(f"GROUP_MAIL record {record.id} missing thread_id - may be old record")
                return None

            # Fetch post from API (same as stream_record)
            response = await self.external_outlook_client.groups_threads_get_post(
                group_id=group_id,
                thread_id=thread_id,
                post_id=post_id
            )

            if not response.success or not response.data:
                self.logger.warning(f"GROUP_MAIL post {post_id} not found at source")
                return None

            post = response.data

            # Fetch thread for topic (need for MailRecord)
            # threads are Pydantic ConversationThread objects from _get_group_threads
            threads = await self._get_group_threads(group_id)
            thread = None
            for t in threads:
                if t.id == thread_id:
                    thread = t
                    break

            if not thread:
                self.logger.warning(f"Thread {thread_id} not found for group {group_id}")
                return None

            # Get group info for permissions
            async with self.data_store_provider.transaction() as tx_store:
                group_data = await tx_store.get_user_group_by_external_id(
                    connector_id=self.connector_id,
                    external_id=group_id
                )

            if not group_data:
                self.logger.warning(f"Group {group_id} not found in database")
                return None

            # Create AppUserGroup for _process_group_post
            group = AppUserGroup(
                app_name=Connectors.OUTLOOK,
                connector_id=self.connector_id,
                source_user_group_id=group_id,
                name=group_data.get('name', OutlookDefaults.UNKNOWN_GROUP_LABEL),
                org_id=org_id,
                description=group_data.get('description')
            )

            # Reuse existing processing logic
            record_update = await self._process_group_post(org_id, group, thread, post)

            if not record_update or not record_update.record:
                return None

            # Check if updated (GROUP_MAIL uses receivedDateTime, no etag)
            if not record_update.is_new and not record_update.is_updated:
                self.logger.debug(f"GROUP_MAIL post {post_id} has not changed at source")
                return None

            return (record_update.record, record_update.new_permissions or [])

        except Exception as e:
            self.logger.error(f"Error fetching GROUP_MAIL post {record.external_record_id}: {e}")
            return None

    async def _check_and_fetch_updated_group_post_attachment(
        self, org_id: str, record: FileRecord
    ) -> tuple[Record, list[Permission]] | None:
        """Fetch group post attachment from source and check for updates."""
        try:
            attachment_id = record.external_record_id
            post_id = record.parent_external_record_id

            if not post_id:
                self.logger.warning(f"GROUP_MAIL attachment {attachment_id} has no parent post ID")
                return None

            # Get parent GROUP_MAIL record to get thread_id (same as stream_record)
            async with self.data_store_provider.transaction() as tx_store:
                parent_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=post_id
                )

            if not parent_record or parent_record.record_type != RecordType.GROUP_MAIL:
                self.logger.warning(f"Parent GROUP_MAIL not found for attachment {attachment_id}")
                return None

            group_id = record.external_record_group_id or parent_record.external_record_group_id
            thread_id = parent_record.thread_id

            if not group_id or not thread_id:
                self.logger.warning(f"GROUP_MAIL attachment {attachment_id} missing group_id or thread_id")
                return None

            # Fetch attachments for this post
            attachments = await self._get_group_post_attachments(group_id, thread_id, post_id)

            # Find our attachment - attachments are Pydantic Attachment objects from _get_group_post_attachments
            attachment = None
            for att in attachments:
                if att.id == attachment_id:
                    attachment = att
                    break

            if not attachment:
                self.logger.warning(f"GROUP_MAIL attachment {attachment_id} not found in post {post_id}")
                return None

            # Check if updated (compare timestamp - no etag for group attachments)
            is_updated = False
            current_modified = attachment.last_modified_date_time
            if current_modified and record.source_updated_at:
                current_ts = datetime_to_epoch_ms(current_modified)
                if current_ts and current_ts > record.source_updated_at:
                    is_updated = True
                    self.logger.info(f"GROUP_MAIL attachment {attachment_id} has changed at source")

            if not is_updated:
                self.logger.debug(f"GROUP_MAIL attachment {attachment_id} has not changed at source")
                return None

            # Get group info for permissions
            async with self.data_store_provider.transaction() as tx_store:
                group_data = await tx_store.get_user_group_by_external_id(
                    connector_id=self.connector_id,
                    external_id=group_id
                )

            if not group_data:
                self.logger.warning(f"Group {group_id} not found in database")
                return None

            # Create group permission (same as sync)
            permission = Permission(
                external_id=group_id,
                type=PermissionType.READ,
                entity_type=EntityType.GROUP,
            )

            # Create FileRecord using updated attachment data
            file_name = attachment.name or OutlookDefaults.ATTACHMENT_NAME
            extension = None
            if '.' in file_name:
                extension = file_name.split('.')[-1].lower()

            content_type = attachment.content_type
            if not content_type:
                return None

            attachment_record = FileRecord(
                id=record.id,
                org_id=org_id,
                record_name=file_name,
                record_type=RecordType.FILE,
                external_record_id=attachment_id,
                version=record.version + 1,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.OUTLOOK,
                connector_id=self.connector_id,
                source_created_at=datetime_to_epoch_ms(attachment.last_modified_date_time),
                source_updated_at=datetime_to_epoch_ms(attachment.last_modified_date_time),
                mime_type=self._get_mime_type_enum(content_type),
                parent_external_record_id=post_id,
                parent_record_type=RecordType.GROUP_MAIL,
                external_record_group_id=group_id,
                record_group_type=RecordGroupType.GROUP_MAILBOX,
                weburl=None,
                is_file=True,
                size_in_bytes=attachment.size or 0,
                extension=extension,
            )

            # Apply indexing filter
            if not self.indexing_filters.is_enabled(IndexingFilterKey.ATTACHMENTS, default=True):
                attachment_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

            return (attachment_record, [permission])

        except Exception as e:
            self.logger.error(f"Error fetching GROUP_MAIL attachment {record.external_record_id}: {e}")
            return None

    async def _check_and_fetch_updated_record(
        self, org_id: str, user_id: str, user_email: str, record: Record
    ) -> tuple[Record, list[Permission]] | None:
        """Fetch record from source and return data for reindexing.

        Args:
            org_id: Organization ID
            user_id: Source user ID for API calls
            user_email: User email for permission extraction
            record: Record to check

        Returns:
            Tuple of (Record, List[Permission]) for processing via on_new_records
        """
        try:
            if record.record_type == RecordType.MAIL:
                return await self._check_and_fetch_updated_email(org_id, user_id, user_email, record)
            elif record.record_type == RecordType.FILE:
                return await self._check_and_fetch_updated_attachment(org_id, user_id, user_email, record)
            else:
                self.logger.warning(f"Unsupported record type for reindex: {record.record_type}")
                return None

        except Exception as e:
            self.logger.error(f"Error checking record {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_email(
        self, org_id: str, user_id: str, user_email: str, record: Record
    ) -> tuple[Record, list[Permission]] | None:
        """Fetch email from source for reindexing."""
        try:
            message_id = record.external_record_id

            message = await self._get_message_by_id_external(user_id, message_id)
            if not message:
                self.logger.warning(f"Email {message_id} not found at source, may have been deleted")
                return None

            folder_id = record.external_record_group_id or ""
            folder_name = OutlookDefaults.UNKNOWN_FOLDER_LABEL

            email_update = await self._process_single_email_with_folder(
                org_id, user_email, message, folder_id, folder_name,
                existing_record=record  # Pass record to skip DB lookup
            )

            if not email_update or not email_update.record:
                return None

            if not email_update.is_new and not email_update.is_updated:
                self.logger.debug(f"Email {message_id} has not changed at source, skipping update")
                return None

            return (email_update.record, email_update.new_permissions or [])

        except Exception as e:
            self.logger.error(f"Error fetching email {record.external_record_id}: {e}")
            return None

    async def _check_and_fetch_updated_attachment(
        self, org_id: str, user_id: str, user_email: str, record: Record
    ) -> tuple[Record, list[Permission]] | None:
        """Fetch attachment from source for reindexing."""
        try:
            attachment_id = record.external_record_id
            parent_message_id = record.parent_external_record_id

            if not parent_message_id:
                self.logger.warning(f"Attachment {attachment_id} has no parent message ID")
                return None

            message = await self._get_message_by_id_external(user_id, parent_message_id)
            if not message:
                self.logger.warning(f"Parent message {parent_message_id} not found at source")
                return None

            # attachments are Pydantic Attachment objects from _get_message_attachments_external
            attachments = await self._get_message_attachments_external(user_id, parent_message_id)

            attachment = None
            for att in attachments:
                if att.id == attachment_id:
                    attachment = att
                    break

            if not attachment:
                self.logger.warning(f"Attachment {attachment_id} not found in parent message")
                return None

            folder_id = record.external_record_group_id or ""

            is_updated = False
            current_revision = attachment.last_modified_date_time.isoformat() if attachment.last_modified_date_time else None
            if record.external_revision_id != current_revision:
                is_updated = True
                self.logger.info(f"Attachment {attachment_id} has changed at source (revision changed)")

            if not is_updated:
                self.logger.debug(f"Attachment {attachment_id} has not changed at source, skipping update")
                return None

            # message is a Pydantic Message object
            email_permissions = await self._extract_email_permissions(message, None, user_email)
            parent_weburl = message.web_link

            attachment_record = await self._create_attachment_record(
                org_id, attachment, parent_message_id, folder_id, existing_record=record, parent_weburl=parent_weburl
            )

            # Return None if attachment was filtered out
            if not attachment_record:
                return None

            return (attachment_record, email_permissions)

        except Exception as e:
            self.logger.error(f"Error fetching attachment {record.external_record_id}: {e}")
            return None

    def _extract_email_from_recipient(self, recipient: object) -> str:
        """Extract email address from a Pydantic Recipient object."""
        if not recipient:
            return ''

        # recipient is a Pydantic Recipient object with email_address property
        if recipient.email_address and recipient.email_address.address:
            return recipient.email_address.address

        # Fallback to empty string
        return ''


    def _get_mime_type_enum(self, content_type: str) -> MimeTypes:
        """Map content type string to MimeTypes enum."""
        content_type_lower = content_type.lower()

        mime_type_map = {
            'text/plain': MimeTypes.PLAIN_TEXT,
            'text/html': MimeTypes.HTML,
            'text/csv': MimeTypes.CSV,
            'application/pdf': MimeTypes.PDF,
            'application/msword': MimeTypes.DOC,
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document': MimeTypes.DOCX,
            'application/vnd.ms-excel': MimeTypes.XLS,
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': MimeTypes.XLSX,
            'application/vnd.ms-powerpoint': MimeTypes.PPT,
            'application/vnd.openxmlformats-officedocument.presentationml.presentation': MimeTypes.PPTX,
        }

        return mime_type_map.get(content_type_lower, MimeTypes.BIN)


    def _format_datetime_string(self, dt_obj: datetime | str | None) -> str:
        """Format datetime object to ISO string."""
        if not dt_obj:
            return ""
        try:
            if isinstance(dt_obj, str):
                return dt_obj
            else:
                return dt_obj.isoformat()
        except Exception:
            return ""

    async def _construct_group_mail_weburl(self, group_id: str) -> str | None:
        """
        Construct web URL for group mail from cached group data or by fetching from API.
        Format: https://outlook.office365.com/groups/{domain}/{mailNickname}/mail

        Args:
            group_id: Group ID to look up

        Returns:
            Constructed web URL or None if data not available
        """
        group_data = self._group_cache.get(group_id)

        # If not cached, fetch from API
        if not group_data:
            if not self.external_users_client:
                return None

            try:
                response = await self.external_users_client.groups_group_get_group(
                    group_id=group_id,
                    select=OutlookAPIFields.GROUP_INFO_SELECT_FIELDS
                )

                if not response.success or not response.data:
                    return None

                # Cache the result for future use - response.data is a Pydantic Group object
                group_data = {
                    'mail': response.data.mail,
                    'mailNickname': response.data.mail_nickname
                }

                if group_data['mail'] and group_data['mailNickname']:
                    self._group_cache[group_id] = group_data
            except Exception as e:
                self.logger.warning(f"Failed to fetch group data for {group_id}: {e}")
                return None

        mail = group_data.get('mail')
        mail_nickname = group_data.get('mailNickname')

        if not mail or not mail_nickname:
            return None

        try:
            # Extract domain from email address
            if '@' not in mail:
                return None
            domain = mail.split('@')[1]

            # Construct URL
            return f"https://outlook.office365.com/groups/{domain}/{mail_nickname}/mail"
        except Exception as e:
            self.logger.warning(f"Failed to construct group mail weburl for group {group_id}: {e}")
            return None

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
    ) -> 'OutlookConnector':
        """Factory method to create and initialize OutlookConnector."""
        data_entities_processor = DataSourceEntitiesProcessor(logger, data_store_provider, config_service)
        await data_entities_processor.initialize()

        return OutlookConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
