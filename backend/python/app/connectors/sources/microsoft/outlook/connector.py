import base64
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from logging import Logger
from typing import AsyncGenerator, Dict, List, Optional, Tuple

from aiolimiter import AsyncLimiter
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

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
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
    generate_record_sync_point_key,
)
from app.connectors.core.registry.connector_builder import (
    AuthField,
    ConnectorBuilder,
    DocumentationLink,
)
from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterCollection,
    FilterField,
    FilterOperator,
    FilterType,
    IndexingFilterKey,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.sources.microsoft.common.apps import OutlookApp
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.models.entities import (
    AppUser,
    FileRecord,
    IndexingStatus,
    MailRecord,
    Record,
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

# Thread detection constants
THREAD_ROOT_EMAIL_CONVERSATION_INDEX_LENGTH = 22  # Length (in bytes) of conversation_index for root email in a thread


@dataclass
class OutlookCredentials:
    tenant_id: str
    client_id: str
    client_secret: str
    has_admin_consent: bool = False


@ConnectorBuilder("Outlook")\
    .in_group("Microsoft 365")\
    .with_auth_type("OAUTH_ADMIN_CONSENT")\
    .with_description("Sync emails from Outlook")\
    .with_categories(["Email"])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/outlook.svg")
        .add_documentation_link(DocumentationLink(
            "Azure AD App Registration Setup",
            "https://docs.microsoft.com/en-us/azure/active-directory/develop/quickstart-register-app",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/microsoft-365/outlook',
            'pipeshub'
        ))
        .with_redirect_uri("connectors/Outlook/oauth/callback", False)
        .add_auth_field(AuthField(
            name="clientId",
            display_name="Application (Client) ID",
            placeholder="Enter your Azure AD Application ID",
            description="The Application (Client) ID from Azure AD App Registration"
        ))
        .add_auth_field(AuthField(
            name="clientSecret",
            display_name="Client Secret",
            placeholder="Enter your Azure AD Client Secret",
            description="The Client Secret from Azure AD App Registration",
            field_type="PASSWORD",
            is_secret=True
        ))
        .add_auth_field(AuthField(
            name="tenantId",
            display_name="Directory (Tenant) ID",
            placeholder="Enter your Azure AD Tenant ID",
            description="The Directory (Tenant) ID from Azure AD"
        ))
        .add_auth_field(AuthField(
            name="hasAdminConsent",
            display_name="Has Admin Consent",
            description="Check if admin consent has been granted for the application",
            field_type="CHECKBOX",
            required=True,
            default_value=False
        ))
        .add_auth_field(AuthField(
            name="redirectUri",
            display_name="Redirect URI",
            placeholder="connectors/Outlook/oauth/callback",
            description="The redirect URI for OAuth authentication",
            field_type="URL",
            required=False,
            max_length=2000
        ))
        .add_conditional_display("redirectUri", "hasAdminConsent", "equals", False)
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .add_filter_field(FilterField(
            name="folders",
            display_name="Folders",
            description="Filter emails by folder names (e.g., Inbox, Sent Items). Leave empty to sync all folders.",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            default_value=[]
        ))
        .add_filter_field(FilterField(
            name="received_date",
            display_name="Received Date",
            description="Filter emails by received date.",
            filter_type=FilterType.DATETIME,
            category=FilterCategory.SYNC
        ))
        .add_filter_field(FilterField(
            name="mails",
            display_name="Index Emails",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of email messages",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="attachments",
            display_name="Index Attachments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of email attachments",
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
        config_service: ConfigurationService
    ) -> None:
        super().__init__(
            OutlookApp(),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
        )
        self.rate_limiter = AsyncLimiter(50, 1)
        self.external_outlook_client: Optional[OutlookCalendarContactsDataSource] = None
        self.external_users_client: Optional[UsersGroupsDataSource] = None
        self.credentials: Optional[OutlookCredentials] = None

        # User cache for performance optimization
        self._user_cache: Dict[str, str] = {}  # email -> source_user_id mapping
        self._user_cache_timestamp: Optional[int] = None
        self._user_cache_ttl: int = 3600  # 1 hour TTL in seconds

        self.email_delta_sync_point = SyncPoint(
            connector_name=Connectors.OUTLOOK,
            org_id=self.data_entities_processor.org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=self.data_store_provider
        )

        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()


    async def init(self) -> bool:
        """Initialize the Outlook connector with credentials and Graph client."""
        try:

            org_id = self.data_entities_processor.org_id

            # Load credentials
            self.credentials = await self._get_credentials(org_id)

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

            # Load filters from config service
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "outlook", self.logger
            )

            # Test connection
            if not await self.test_connection_and_access():
                self.logger.error("Outlook connector connection test failed")
                return False

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
                    select=["id"]
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

    async def _get_credentials(self, org_id: str) -> OutlookCredentials:
        """Load Outlook credentials from configuration."""
        try:
            config_path = "/services/connectors/outlook/config"
            config = await self.config_service.get_config(config_path)

            if not config:
                raise ValueError("Outlook configuration not found")

            return OutlookCredentials(
                tenant_id=config["auth"]["tenantId"],
                client_id=config["auth"]["clientId"],
                client_secret=config["auth"]["clientSecret"],
                has_admin_consent=config["auth"].get("hasAdminConsent", False),
            )
        except Exception as e:
            self.logger.error(f"Failed to load Outlook credentials: {e}")
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

    async def _get_user_id_from_email(self, email: str) -> Optional[str]:
        """Get user ID from email using cache."""
        try:
            # Ensure cache is populated
            await self._populate_user_cache()

            return self._user_cache.get(email.lower())
        except Exception as e:
            self.logger.error(f"Error getting user ID from cache for {email}: {e}")
            return None


    async def run_sync(self) -> None:
        """Run full Outlook sync - emails and attachments from all folders."""
        try:
            org_id = self.data_entities_processor.org_id
            self.logger.info("Starting Outlook email sync...")

            # Ensure external clients are initialized
            if not self.external_outlook_client or not self.external_users_client:
                raise Exception("External API clients not initialized. Call init() first.")

            all_enterprise_users = await self._get_all_users_external()
            all_active_users = await self.data_entities_processor.get_all_active_users()

            # Create a mapping of email to source_user_id from enterprise users
            email_to_source_id = {
                user.email.lower(): user.source_user_id
                for user in all_enterprise_users
                if user.email
            }

            # Get active users that exist in enterprise users and add source_user_id
            users_to_sync = []
            for user in all_active_users:
                if user.email and user.email.lower() in email_to_source_id:
                    user.source_user_id = email_to_source_id[user.email.lower()]
                    users_to_sync.append(user)

            # Populate user cache during sync for better performance
            await self._populate_user_cache()

            await self.data_entities_processor.on_new_app_users(all_enterprise_users)

            async for status in self._process_users(org_id, users_to_sync):
                self.logger.info(status)

            self.logger.info("Outlook sync completed successfully")

        except Exception as e:
            self.logger.error(f"Error during Outlook sync: {e}")
            raise

    async def _get_all_users_external(self) -> List[AppUser]:
        """Get all users using external Users Groups API."""
        try:
            if not self.external_users_client:
                raise Exception("External Users Groups client not initialized")

            # Use external API to get users
            response: UsersGroupsResponse = await self.external_users_client.users_user_list_user()

            if not response.success or not response.data:
                self.logger.error(f"Failed to get users: {response.error}")
                return []

            users = []
            # Handle UserCollectionResponse object
            user_data = self._safe_get_attr(response.data, 'value', [])

            for user in user_data:
                display_name = self._safe_get_attr(user, 'display_name') or ''
                given_name = self._safe_get_attr(user, 'given_name') or ''
                surname = self._safe_get_attr(user, 'surname') or ''

                # Create full_name from available name parts
                full_name = display_name if display_name else f"{given_name} {surname}".strip()
                if not full_name:
                    full_name = self._safe_get_attr(user, 'mail') or self._safe_get_attr(user, 'user_principal_name') or 'Unknown User'

                app_user = AppUser(
                    app_name=Connectors.OUTLOOK,
                    source_user_id=self._safe_get_attr(user, 'id'),
                    email=self._safe_get_attr(user, 'mail') or self._safe_get_attr(user, 'user_principal_name'),
                    full_name=full_name
                )
                users.append(app_user)

            return users

        except Exception as e:
            self.logger.error(f"Error getting users from external API: {e}")
            return []

    async def _process_users(self, org_id: str, users: List[AppUser]) -> AsyncGenerator[str, None]:
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
            user_id = user.source_user_id

            # Extract folder filter parameters for API-level filtering
            folder_names: Optional[List[str]] = None
            folder_filter_mode: Optional[str] = None

            folders_filter = self.sync_filters.get(SyncFilterKey.FOLDERS)
            if folders_filter and not folders_filter.is_empty():
                filter_value = folders_filter.get_value()
                filter_operator = folders_filter.get_operator()

                if filter_value and isinstance(filter_value, list):
                    folder_names = filter_value
                    if filter_operator == FilterOperator.IN:
                        folder_filter_mode = "include"
                        self.logger.info(f"Filtering to include folders: {folder_names}")
                    elif filter_operator == FilterOperator.NOT_IN:
                        folder_filter_mode = "exclude"
                        self.logger.info(f"Filtering to exclude folders: {folder_names}")

            # Get folders for this user with optional filtering
            folders = await self._get_all_folders_for_user(
                user_id,
                folder_names=folder_names,
                folder_filter_mode=folder_filter_mode
            )

            if not folders:
                return f"No folders found for {user.email}" + (" after applying filters" if folder_names else "")

            total_processed = 0
            folder_results = []
            all_mail_records = []  # Collect all mail records for email thread edges processing

            # Process folders sequentially instead of concurrently
            for folder in folders:
                folder_name = self._safe_get_attr(folder, 'display_name', 'Unnamed Folder')
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

    async def _find_parent_by_conversation_index_from_db(self, conversation_index: str, thread_id: str, org_id: str, user: AppUser) -> Optional[str]:
        """Find parent message ID using conversation index by searching ArangoDB."""
        if not conversation_index:
            self.logger.debug(f"No conversation_index provided for thread {thread_id}")
            return None

        try:
            # Decode conversation index
            index_bytes = base64.b64decode(conversation_index)

            # Root message (22 bytes) has no parent
            if len(index_bytes) <= THREAD_ROOT_EMAIL_CONVERSATION_INDEX_LENGTH:
                return None

            # Get parent index by removing last 5 bytes
            parent_bytes = index_bytes[:-5]
            parent_index = base64.b64encode(parent_bytes).decode('utf-8')
            self.logger.debug(f"Thread {thread_id}: Looking for parent with conversation_index={parent_index}")

            # Search in ArangoDB for parent message
            async with self.data_store_provider.transaction() as tx_store:
                parent_record = await tx_store.get_record_by_conversation_index(
                    connector_name=Connectors.OUTLOOK,
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

    async def _create_all_thread_edges_for_user(self, org_id: str, user: AppUser, user_mail_records: List[Record]) -> int:
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
                            "_from": f"records/{parent_id}",
                            "_to": f"records/{record.id}",
                            "relationType": "SIBLING"
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

    async def _get_all_folders_for_user(
        self,
        user_id: str,
        folder_names: Optional[List[str]] = None,
        folder_filter_mode: Optional[str] = None
    ) -> List[Dict]:
        """Get all top-level folders for a user with optional filtering.

        Args:
            user_id: User identifier
            folder_names: Optional list of folder display names to filter
            folder_filter_mode: 'include' to whitelist or 'exclude' to blacklist folder_names

        Returns:
            List of folder dictionaries
        """
        try:
            if not self.external_outlook_client:
                raise Exception("External Outlook client not initialized")

            # Use API-level filtering with eq/ne operators
            response: OutlookMailFoldersResponse = await self.external_outlook_client.users_list_mail_folders(
                user_id=user_id,
                folder_names=folder_names,
                folder_filter_mode=folder_filter_mode,
            )

            if not response.success:
                self.logger.error(f"Failed to get folders: {response.error}")
                return []

            data = response.data or {}
            folders = data.get('value', [])

            return folders
        except Exception as e:
            self.logger.error(f"Error getting folders for user {user_id}: {e}")
            return []

    async def _process_single_folder_messages(self, org_id: str, user: AppUser, folder: Dict) -> tuple[int, List[Record]]:
        """Process messages using batch processing with automatic pagination."""
        try:
            user_id = user.source_user_id
            folder_id = self._safe_get_attr(folder, 'id')
            folder_name = self._safe_get_attr(folder, 'display_name', 'Unnamed Folder')

            # Create folder-specific sync point
            sync_point_key = generate_record_sync_point_key(
                RecordType.MAIL.value, "folders", f"{user_id}_{folder_id}"
            )
            sync_point = await self.email_delta_sync_point.read_sync_point(sync_point_key)
            delta_link = sync_point.get('delta_link') if sync_point else None

            # Get messages for this folder using delta sync
            result = await self._get_all_messages_delta_external(user_id, folder_id, delta_link)
            messages = result['messages']

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
            batch_size = 50

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
                'delta_link': result.get('delta_link'),
                'last_sync_timestamp': int(datetime.now(timezone.utc).timestamp() * 1000),
                'folder_id': folder_id,
                'folder_name': folder_name
            }

            await self.email_delta_sync_point.update_sync_point(sync_point_key, sync_point_data)

            # Log final summary
            self.logger.info(f"Folder '{folder_name}' completed: {processed_count} records processed from {len(messages)} messages")

            return processed_count, mail_records

        except Exception as e:
            self.logger.error(f"Error processing messages in folder '{folder_name}' for user {user.email}: {e}")
            return 0, []

    async def _get_all_messages_delta_external(self, user_id: str, folder_id: str, delta_link: Optional[str] = None) -> Dict:
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
            Dict with:
                - messages: List of all messages across all pages
                - delta_link: New deltaLink to save for next sync
        """
        try:
            if not self.external_outlook_client:
                raise Exception("External Outlook client not initialized")

            # Build filter string for receivedDateTime if configured
            # Note: MS Graph delta queries have limited filter support
            # receivedDateTime filter only supports 'ge' (greater than or equal)
            # For 'le' (IS_BEFORE), we apply client-side filtering after fetching
            filter_string = None
            received_before_dt: Optional[datetime] = None  # For client-side filtering

            received_date_filter = self.sync_filters.get(SyncFilterKey.RECEIVED_DATE)
            if received_date_filter and not received_date_filter.is_empty():
                received_after_iso, received_before_iso = received_date_filter.get_datetime_iso()

                # API supports 'ge' (greater than or equal) - apply server-side
                if received_after_iso:
                    filter_string = f"receivedDateTime ge {received_after_iso}Z"
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
                page_size=100,
                filter=filter_string,
                select = [
                    'id',
                    'subject',
                    'hasAttachments',
                    'createdDateTime',
                    'lastModifiedDateTime',
                    'receivedDateTime',
                    'webLink',
                    'from',
                    'toRecipients',
                    'ccRecipients',
                    'bccRecipients',
                    'conversationId',
                    'internetMessageId',
                    'conversationIndex'
                ]
            )

            # Apply client-side filtering for IS_BEFORE if needed
            if received_before_dt is not None and messages:
                original_count = len(messages)
                filtered_messages = []

                for msg in messages:
                    # Get receivedDateTime from message
                    received_dt = self._safe_get_attr(msg, 'received_date_time')
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

            return {
                'messages': messages,
                'delta_link': new_delta_link
            }

        except Exception as e:
            self.logger.error(f"Error getting messages delta for folder {folder_id}: {e}", exc_info=True)
            return {'messages': [], 'delta_link': None, 'next_link': None}

    async def _process_single_message(self, org_id: str, user: AppUser, message, folder_id: str, folder_name: str) -> List[RecordUpdate]:
        """Process one message and its attachments together."""
        updates = []

        try:
            message_id = self._safe_get_attr(message, 'id')

            # Check if message is deleted
            additional_data = self._safe_get_attr(message, 'additional_data', {})
            is_deleted = (additional_data.get('@removed', {}).get('reason') == 'deleted')

            if is_deleted:
                self.logger.info(f"Deleting message: {message_id} and its attachments from folder {folder_name}")
                async with self.data_store_provider.transaction() as tx_store:
                    await tx_store.delete_record_by_external_id(Connectors.OUTLOOK, message_id, user.user_id)
                return updates

            # Process email with attachments
            email_update = await self._process_single_email_with_folder(org_id, user.email, message, folder_id, folder_name)
            if email_update:
                updates.append(email_update)

                # Process attachments if any
                has_attachments = self._safe_get_attr(message, 'has_attachments', False)
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
            self.logger.error(f"Error processing message {self._safe_get_attr(message, 'id', 'unknown')}: {e}")

        return updates

    async def _process_single_email_with_folder(
        self, org_id: str, user_email: str, message, folder_id: str, folder_name: str,
        existing_record: Optional[Record] = None
    ) -> Optional[RecordUpdate]:
        """Process a single email with folder information.

        Args:
            existing_record: Optional existing record to skip DB lookup (used during reindex)
        """
        try:
            message_id = self._safe_get_attr(message, 'id')

            # Skip DB lookup if existing_record is provided (reindex case)
            if existing_record is None:
                existing_record = await self._get_existing_record(org_id, message_id)
            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False

            if not is_new:
                current_etag = self._safe_get_attr(message, 'e_tag')
                if existing_record.external_revision_id != current_etag:
                    content_changed = True
                    is_updated = True
                    self.logger.info(f"Email {message_id} content changed (e_tag: {existing_record.external_revision_id} -> {current_etag})")

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
                record_name=self._safe_get_attr(message, 'subject', 'No Subject') or 'No Subject',
                record_type=RecordType.MAIL,
                external_record_id=message_id,
                external_revision_id=self._safe_get_attr(message, 'e_tag'),
                version=0 if is_new else existing_record.version + 1,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.OUTLOOK,
                source_created_at=self._parse_datetime(self._safe_get_attr(message, 'created_date_time')),
                source_updated_at=self._parse_datetime(self._safe_get_attr(message, 'last_modified_date_time')),
                weburl=self._safe_get_attr(message, 'web_link', ''),
                mime_type=MimeTypes.HTML.value,
                parent_external_record_id=None,
                external_record_group_id=folder_id,
                record_group_type=RecordGroupType.MAILBOX,
                subject=self._safe_get_attr(message, 'subject', 'No Subject') or 'No Subject',
                from_email=self._extract_email_from_recipient(self._safe_get_attr(message, 'from_', None)),
                to_emails=[self._extract_email_from_recipient(r) for r in self._safe_get_attr(message, 'to_recipients', [])],
                cc_emails=[self._extract_email_from_recipient(r) for r in self._safe_get_attr(message, 'cc_recipients', [])],
                bcc_emails=[self._extract_email_from_recipient(r) for r in self._safe_get_attr(message, 'bcc_recipients', [])],
                thread_id=self._safe_get_attr(message, 'conversation_id', ''),
                is_parent=False,
                internet_message_id=self._safe_get_attr(message, 'internet_message_id', ''),
                conversation_index=self._safe_get_attr(message, 'conversation_index', ''),
            )

            # Apply indexing filter for mail records
            if not self.indexing_filters.is_enabled(IndexingFilterKey.MAILS, default=True):
                email_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

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
            self.logger.error(f"Error processing email {self._safe_get_attr(message, 'id', 'unknown')}: {str(e)}")
            return None

    async def _extract_email_permissions(self, message: Dict, record_id: Optional[str], inbox_owner_email: str) -> List[Permission]:
        """Extract permissions from email recipients, with special handling for inbox owner."""
        permissions = []

        try:
            all_recipients = []
            all_recipients.extend(self._safe_get_attr(message, 'to_recipients', []))
            all_recipients.extend(self._safe_get_attr(message, 'cc_recipients', []))
            all_recipients.extend(self._safe_get_attr(message, 'bcc_recipients', []))

            # Add sender as well (they have access to the email)
            from_recipient = self._safe_get_attr(message, 'from_')
            if from_recipient:
                all_recipients.append(from_recipient)

            # Create a set to track unique email addresses
            processed_emails = set()
            inbox_owner_email_lower = inbox_owner_email.lower()

            for recipient in all_recipients:
                try:
                    email_address = self._extract_email_from_recipient(recipient)
                    if email_address and email_address not in processed_emails:
                        processed_emails.add(email_address)

                        if email_address.lower() == inbox_owner_email_lower:
                            permission_type = PermissionType.OWNER
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

            return permissions

        except Exception as e:
            self.logger.error(f"Error extracting permissions: {e}")
            return []

    async def _create_attachment_record(
        self,
        org_id: str,
        attachment: Dict,
        message_id: str,
        folder_id: str,
        existing_record: Optional[Record] = None,
        parent_weburl: Optional[str] = None,
    ) -> FileRecord:
        """Helper method to create a FileRecord from an attachment.

        Args:
            org_id: Organization ID
            attachment: Attachment data from Microsoft Graph API
            message_id: Parent message ID
            folder_id: Folder ID
            existing_record: Existing record if updating
            parent_weburl: Web URL of the parent mail

        Returns:
            FileRecord: Created attachment record
        """
        attachment_id = self._safe_get_attr(attachment, 'id')
        is_new = existing_record is None

        content_type = self._safe_get_attr(attachment, 'content_type', 'application/octet-stream')
        mime_type = self._get_mime_type_enum(content_type)

        file_name = self._safe_get_attr(attachment, 'name', 'Unnamed Attachment')
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
            external_revision_id=self._safe_get_attr(attachment, 'e_tag'),
            version=0 if is_new else existing_record.version + 1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.OUTLOOK,
            source_created_at=self._parse_datetime(self._safe_get_attr(attachment, 'last_modified_date_time')),
            source_updated_at=self._parse_datetime(self._safe_get_attr(attachment, 'last_modified_date_time')),
            mime_type=mime_type,
            parent_external_record_id=message_id,
            parent_record_type=RecordType.MAIL,
            external_record_group_id=folder_id,
            record_group_type=RecordGroupType.MAILBOX,
            weburl=parent_weburl,
            is_file=True,
            size_in_bytes=self._safe_get_attr(attachment, 'size', 0),
            extension=extension,
        )

        # Apply indexing filter for attachment records
        if not self.indexing_filters.is_enabled(IndexingFilterKey.ATTACHMENTS, default=True):
            attachment_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

        return attachment_record

    async def _process_email_attachments_with_folder(self, org_id: str, user: AppUser, message: Dict,
                                                  email_permissions: List[Permission], folder_id: str, folder_name: str) -> List[RecordUpdate]:
        """Process email attachments with folder information."""
        attachment_updates = []

        try:
            user_id = user.source_user_id
            message_id = self._safe_get_attr(message, 'id')
            parent_weburl = self._safe_get_attr(message, 'web_link')

            attachments = await self._get_message_attachments_external(user_id, message_id)

            for attachment in attachments:
                attachment_id = self._safe_get_attr(attachment, 'id')
                existing_record = await self._get_existing_record(org_id, attachment_id)
                is_new = existing_record is None
                is_updated = False
                metadata_changed = False
                content_changed = False

                if not is_new:
                    current_etag = self._safe_get_attr(attachment, 'e_tag')
                    if existing_record.external_revision_id != current_etag:
                        content_changed = True
                        is_updated = True
                        self.logger.info(f"Attachment {attachment_id} content changed (e_tag changed)")

                    current_folder_id = folder_id
                    existing_folder_id = existing_record.external_record_group_id
                    if existing_folder_id and current_folder_id != existing_folder_id:
                        metadata_changed = True
                        is_updated = True

                attachment_record = await self._create_attachment_record(
                    org_id, attachment, message_id, folder_id, existing_record, parent_weburl
                )

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
            self.logger.error(f"Error processing attachments for email {self._safe_get_attr(message, 'id', 'unknown')}: {e}")
            return []

    async def _get_message_attachments_external(self, user_id: str, message_id: str) -> List[Dict]:
        """Get message attachments using external Outlook API."""
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

            # Handle response object (similar to users and messages)
            return self._safe_get_attr(response.data, 'value', [])

        except Exception as e:
            self.logger.error(f"Error getting attachments for message {message_id}: {e}")
            return []

    async def _get_existing_record(self, org_id: str, external_record_id: str) -> Optional[Record]:
        """Get existing record from data store."""
        try:
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_name=Connectors.OUTLOOK,
                    external_id=external_record_id
                )
                return existing_record
        except Exception as e:
            self.logger.error(f"Error getting existing record {external_record_id}: {e}")
            return None

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Stream record content (email or attachment)."""
        try:
            if not self.external_outlook_client:
                raise HTTPException(status_code=500, detail="External Outlook client not initialized")

            # Get the mailbox owner's Graph User ID from permission edges
            user_id = None

            async with self.data_store_provider.transaction() as tx_store:
                user_email = await tx_store.get_record_owner_source_user_email(record.id)
                user_id = await self._get_user_id_from_email(user_email)

                if user_id:
                    self.logger.debug(f"Found Graph user ID {user_id} for record {record.id} from permission edges")
                else:
                    self.logger.warning(f"Could not find owner for record {record.id} in permission edges")

            if not user_id:
                raise HTTPException(status_code=400, detail="Could not determine user context for this record.")

            if record.record_type == RecordType.MAIL:
                message = await self._get_message_by_id_external(user_id, record.external_record_id)
                # Extract email body content from ItemBody object
                body_obj = self._safe_get_attr(message, 'body')
                email_body = self._safe_get_attr(body_obj, 'content', '') if body_obj else ''

                async def generate_email() -> AsyncGenerator[bytes, None]:
                    yield email_body.encode('utf-8')

                return StreamingResponse(generate_email(), media_type='text/html')

            elif record.record_type == RecordType.FILE:
                # Download attachment using stored parent message ID
                attachment_id = record.external_record_id
                parent_message_id = record.parent_external_record_id

                if not parent_message_id:
                    raise HTTPException(status_code=404, detail="No parent message ID stored for attachment")

                attachment_data = await self._download_attachment_external(user_id, parent_message_id, attachment_id)

                async def generate_attachment() -> AsyncGenerator[bytes, None]:
                    yield attachment_data

                # Set proper filename and content type
                filename = record.record_name or "attachment"
                headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
                media_type = record.mime_type or 'application/octet-stream'

                return StreamingResponse(generate_attachment(), media_type=media_type, headers=headers)

            else:
                raise HTTPException(status_code=400, detail="Unsupported record type for streaming")

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to stream record: {str(e)}")

    async def _get_message_by_id_external(self, user_id: str, message_id: str) -> Dict:
        """Get a specific message by ID using external Outlook API."""
        try:
            if not self.external_outlook_client:
                raise Exception("External Outlook client not initialized")

            response: OutlookCalendarContactsResponse = await self.external_outlook_client.users_get_messages(
                user_id=user_id,
                message_id=message_id
            )

            if not response.success:
                self.logger.error(f"Failed to get message {message_id}: {response.error}")
                return {}

            return response.data or {}

        except Exception as e:
            self.logger.error(f"Error getting message {message_id}: {e}")
            return {}

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

            # Extract attachment content from FileAttachment object
            attachment_data = response.data
            content_bytes = (self._safe_get_attr(attachment_data, 'content_bytes') or
                           self._safe_get_attr(attachment_data, 'contentBytes'))

            if not content_bytes:
                return b''

            # Decode base64 content
            return base64.b64decode(content_bytes)

        except Exception as e:
            self.logger.error(f"Error downloading attachment {attachment_id} for message {message_id}: {e}")
            return b''


    def get_signed_url(self, record: Record) -> Optional[str]:
        """Get signed URL for record access. Not supported for Outlook."""
        return None


    async def handle_webhook_notification(self, org_id: str, notification: Dict) -> bool:
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

    async def reindex_records(self, records: List[Record]) -> None:
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

            # Group records by owner email for efficient processing
            records_by_user: Dict[str, List[Record]] = {}
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
            all_updated_records_with_permissions: List[Tuple[Record, List[Permission]]] = []
            all_non_updated_records: List[Record] = []

            # Process records by user - check for source updates
            for user_email, user_records in records_by_user.items():
                try:
                    updated, non_updated = await self._reindex_user_records(user_email, user_records)
                    all_updated_records_with_permissions.extend(updated)
                    all_non_updated_records.extend(non_updated)
                except Exception as e:
                    self.logger.error(f"Error reindexing records for user {user_email}: {e}")

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

    async def _reindex_user_records(
        self, user_email: str, records: List[Record]
    ) -> Tuple[List[Tuple[Record, List[Permission]]], List[Record]]:
        """Reindex records for a specific user. Checks source for updates.

        Returns:
            Tuple of (updated_records_with_permissions, non_updated_records)
        """
        updated_records_with_permissions: List[Tuple[Record, List[Permission]]] = []
        non_updated_records: List[Record] = []

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

    async def _check_and_fetch_updated_record(
        self, org_id: str, user_id: str, user_email: str, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
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
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch email from source for reindexing."""
        try:
            message_id = record.external_record_id

            message = await self._get_message_by_id_external(user_id, message_id)
            if not message:
                self.logger.warning(f"Email {message_id} not found at source, may have been deleted")
                return None

            folder_id = record.external_record_group_id or ""
            folder_name = "Unknown"

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
    ) -> Optional[Tuple[Record, List[Permission]]]:
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

            attachments = await self._get_message_attachments_external(user_id, parent_message_id)

            attachment = None
            for att in attachments:
                if self._safe_get_attr(att, 'id') == attachment_id:
                    attachment = att
                    break

            if not attachment:
                self.logger.warning(f"Attachment {attachment_id} not found in parent message")
                return None

            folder_id = record.external_record_group_id or ""

            is_updated = False
            current_etag = self._safe_get_attr(attachment, 'e_tag')
            if record.external_revision_id != current_etag:
                is_updated = True
                self.logger.info(f"Attachment {attachment_id} has changed at source (e_tag changed)")

            if not is_updated:
                self.logger.debug(f"Attachment {attachment_id} has not changed at source, skipping update")
                return None

            email_permissions = await self._extract_email_permissions(message, None, user_email)
            parent_weburl = self._safe_get_attr(message, 'web_link')

            attachment_record = await self._create_attachment_record(
                org_id, attachment, parent_message_id, folder_id, existing_record=record, parent_weburl=parent_weburl
            )

            return (attachment_record, email_permissions)

        except Exception as e:
            self.logger.error(f"Error fetching attachment {record.external_record_id}: {e}")
            return None

    def _extract_email_from_recipient(self, recipient) -> str:
        """Extract email address from a Recipient object."""
        if not recipient:
            return ''

        # Handle Recipient objects with emailAddress property
        email_addr = self._safe_get_attr(recipient, 'email_address') or self._safe_get_attr(recipient, 'emailAddress')
        if email_addr:
            return self._safe_get_attr(email_addr, 'address', '')

        # Fallback to string representation
        return str(recipient) if recipient else ''

    def _safe_get_attr(self, obj, attr_name: str, default=None) -> Optional[object]:
        """Safely get attribute from object that could be a class instance or dictionary."""
        if hasattr(obj, attr_name):
            return getattr(obj, attr_name, default)
        elif hasattr(obj, 'get'):
            return obj.get(attr_name, default)
        else:
            return default

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

    def _parse_datetime(self, dt_obj) -> Optional[int]:
        """Parse datetime object or string to epoch timestamp in milliseconds."""
        if not dt_obj:
            return None
        try:
            if isinstance(dt_obj, str):
                dt = datetime.fromisoformat(dt_obj.replace('Z', '+00:00'))
            else:
                dt = dt_obj
            return int(dt.timestamp() * 1000)
        except Exception:
            return None

    def _format_datetime_string(self, dt_obj) -> str:
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


    @classmethod
    async def create_connector(cls, logger: Logger, data_store_provider: DataStoreProvider, config_service: ConfigurationService) -> 'OutlookConnector':
        """Factory method to create and initialize OutlookConnector."""
        data_entities_processor = DataSourceEntitiesProcessor(logger, data_store_provider, config_service)
        await data_entities_processor.initialize()

        return OutlookConnector(logger, data_entities_processor, data_store_provider, config_service)
