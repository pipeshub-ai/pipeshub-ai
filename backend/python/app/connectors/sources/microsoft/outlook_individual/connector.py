import base64
import html
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import datetime, timezone
from logging import Logger

from aiolimiter import AsyncLimiter
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
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
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import (
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
    FilterOption,
    FilterOptionsResponse,
    FilterType,
    IndexingFilterKey,
    MultiselectOperator,
    OptionSourceType,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.sources.microsoft.common.apps import OutlookIndividualApp
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.models.entities import (
    AppUser,
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
    MSGraphClientWithDelegatedAuth,
)
from app.sources.client.microsoft.microsoft import (
    MSGraphClient as ExternalMSGraphClient,
)
from app.sources.external.microsoft.outlook.outlook import (
    OutlookCalendarContactsDataSource,
    OutlookCalendarContactsResponse,
    OutlookMailFoldersResponse,
)
from app.utils.oauth_config import fetch_oauth_config_by_id
from app.utils.streaming import create_stream_record_response

# Thread detection constants
THREAD_ROOT_EMAIL_CONVERSATION_INDEX_LENGTH = 22  # Length (in bytes) of conversation_index for root email in a thread

# Standard Outlook folder names
STANDARD_OUTLOOK_FOLDERS = [
    "Inbox",
    "Sent Items",
    "Drafts",
    "Deleted Items",
    "Junk Email",
    "Archive",
    "Outbox",
    "Conversation History"
]


@dataclass
class OutlookCredentials:
    tenant_id: str
    client_id: str
    client_secret: str
    has_admin_consent: bool = False


@ConnectorBuilder("Outlook Personal")\
    .in_group("Microsoft 365")\
    .with_description("Sync emails from your personal Outlook mailbox")\
    .with_categories(["Email"])\
    .with_scopes([ConnectorScope.PERSONAL.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Outlook Personal",
            authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
            redirect_uri="connectors/oauth/callback/OutlookPersonal",
            scopes=OAuthScopeConfig(
                personal_sync=[
                    "https://graph.microsoft.com/Mail.Read",
                    "https://graph.microsoft.com/User.Read",
                    "offline_access",
                ],
                team_sync=[],
                agent=[]
            ),
            fields=[
                CommonFields.tenant_id("Azure AD App Registration"),
                CommonFields.client_id("Azure AD App Registration"),
                CommonFields.client_secret("Azure AD App Registration")
            ],
            icon_path="/assets/icons/connectors/outlook.svg",
            app_group="Microsoft 365",
            app_description="OAuth application for accessing Outlook mailbox API",
            app_categories=["Email"],
            additional_params={
                "response_mode": "query",
                "prompt": "select_account",
            }
        )
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/outlook.svg")
        .add_documentation_link(DocumentationLink(
            "Azure AD App Registration Setup",
            "https://docs.microsoft.com/en-us/azure/active-directory/develop/quickstart-register-app",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/microsoft-365/outlook-personal',
            'pipeshub'
        ))
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .add_filter_field(FilterField(
            name=SyncFilterKey.FOLDERS.value,
            display_name="Mail Folders",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description="Select folders to sync. Leave empty to sync all folders.",
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
    )\
    .build_decorator()
class OutlookIndividualConnector(BaseConnector):
    """Microsoft Outlook Personal connector for syncing emails from your mailbox."""

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str
    ) -> None:
        super().__init__(
            OutlookIndividualApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id
        )
        self.rate_limiter = AsyncLimiter(50, 1)
        self.external_outlook_client: OutlookCalendarContactsDataSource | None = None
        self.credentials: OutlookCredentials | None = None
        self.connector_id = connector_id

        # Personal connector attributes
        self.connector_scope: str | None = None
        self.created_by: str | None = None
        self.creator_email: str | None = None

        self.email_delta_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=self.data_entities_processor.org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=self.data_store_provider
        )

        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()


    async def init(self) -> bool:
        """Initialize the Outlook Personal connector with credentials and Graph client."""
        try:
            connector_id = self.connector_id

            # Read connector scope and creator info from config
            config_path = f"/services/connectors/{connector_id}/config"
            config = await self.config_service.get_config(config_path)

            if not config:
                raise ValueError(f"Configuration not found for connector {connector_id}")

            self.connector_scope = config.get("scope", "PERSONAL")

            # Get creator user from connector instance (uses proper abstraction)
            creator_user = await self.data_entities_processor.get_app_creator_user(self.connector_id)
            if not creator_user:
                raise ValueError(f"Creator user not found for connector {self.connector_id}")

            self.created_by = creator_user.user_id
            self.creator_email = creator_user.email

            self.logger.info(
                f"Initializing Outlook Personal connector for creator: {self.creator_email} "
                f"(user_id: {self.created_by})"
            )

            # Load credentials (OAuth config with tenant_id, client_id, client_secret)
            self.credentials = await self._get_credentials(connector_id)

            # Get access token from connector config
            credentials_data = config.get("credentials", {})
            access_token = credentials_data.get("access_token")

            if not access_token:
                raise ValueError("Access token not found for personal Outlook connector. Please complete OAuth flow.")

            # Create delegated auth client directly (for personal connector with user OAuth)
            delegated_client = MSGraphClientWithDelegatedAuth(
                access_token=access_token,
                tenant_id=self.credentials.tenant_id,
                logger=self.logger
            )
            self.external_client = ExternalMSGraphClient(delegated_client, mode=GraphMode.DELEGATED)

            # Create Outlook data source client
            self.external_outlook_client = OutlookCalendarContactsDataSource(self.external_client)

            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize Outlook Personal connector: {e}")
            return False

    async def test_connection_and_access(self) -> bool:
        """Test connection by fetching current user's mail folders via delegated auth."""
        try:
            if not self.external_outlook_client:
                self.logger.error("External outlook client not initialized")
                return False

            # Test by listing mail folders with minimal fields
            response = await self.external_outlook_client.me_list_mail_folders(
                top=1,
                select=["id", "displayName"]
            )

            if not response.success:
                self.logger.error(f"Connection test failed: {response.error}")
                return False

            self.logger.info("Outlook Personal connector connection test passed")
            return True

        except Exception as e:
            self.logger.error(f"Connection test failed: {e}")
            return False

    async def _get_credentials(self, connector_id: str) -> OutlookCredentials:
        """Load Outlook credentials from configuration."""
        try:
            config_path = f"/services/connectors/{connector_id}/config"
            config = await self.config_service.get_config(config_path)

            if not config:
                raise ValueError(f"Outlook configuration not found for connector {connector_id}")

            # Extract auth configuration
            auth_config = config.get("auth", {})
            oauth_config_id = auth_config.get("oauthConfigId")

            if not oauth_config_id:
                self.logger.error("Outlook Personal oauthConfigId not found in auth configuration.")
                raise ValueError("Outlook Personal oauthConfigId not found in auth configuration.")

            # Fetch OAuth config
            oauth_config = await fetch_oauth_config_by_id(
                oauth_config_id=oauth_config_id,
                connector_type="Outlook Personal",
                config_service=self.config_service,
                logger=self.logger
            )

            if not oauth_config:
                self.logger.error(f"OAuth config {oauth_config_id} not found for Outlook Personal connector.")
                raise ValueError(f"OAuth config {oauth_config_id} not found for Outlook Personal connector.")

            oauth_config_data = oauth_config.get("config", {})

            tenant_id = oauth_config_data.get("tenantId")
            client_id = oauth_config_data.get("clientId")
            client_secret = oauth_config_data.get("clientSecret")

            if not all((tenant_id, client_id, client_secret)):
                self.logger.error("Incomplete Outlook Personal OAuth config. Ensure tenantId, clientId and clientSecret are configured.")
                raise ValueError("Incomplete Outlook Personal credentials. Ensure tenantId, clientId and clientSecret are configured.")

            return OutlookCredentials(
                tenant_id=tenant_id,
                client_id=client_id,
                client_secret=client_secret,
                has_admin_consent=False,
            )
        except Exception as e:
            self.logger.error(f"Failed to load Outlook credentials for connector {connector_id}: {e}")
            raise

    async def run_sync(self) -> None:
        """Run Outlook Personal sync - emails and attachments for creator only."""
        try:
            org_id = self.data_entities_processor.org_id
            self.logger.info(f"Starting Outlook Personal sync for creator: {self.creator_email}...")

            # Load filters from config service (do this in run_sync to get latest filter values)
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "outlookindividual", self.connector_id, self.logger
            )

            # Ensure external client is initialized
            if not self.external_outlook_client:
                raise Exception("External Outlook client not initialized. Call init() first.")

            # Get creator as AppUser
            creator_user = await self._sync_users()

            # Process emails for creator
            await self._process_user_emails(org_id, creator_user)

            self.logger.info("Outlook Personal sync completed successfully")

        except Exception as e:
            self.logger.error(f"Error during Outlook Personal sync: {e}")
            raise

    async def _sync_users(self) -> AppUser:
        """Get creator as AppUser for personal connector."""
        try:
            self.logger.info(f"Setting up creator user: {self.creator_email}...")

            if not self.creator_email or not self.created_by:
                raise ValueError("Creator email and user ID are required")

            name = self.creator_email.split('@')[0].replace('.', ' ').title()

            # Create AppUser for creator
            creator_app_user = AppUser(
                app_name=Connectors.OUTLOOK_INDIVIDUAL,
                connector_id=self.connector_id,
                source_user_id=self.created_by,
                email=self.creator_email,
                full_name=name
            )

            # Sync creator to database
            await self.data_entities_processor.on_new_app_users([creator_app_user])

            self.logger.info(f"✅ Creator user ready for sync: {self.creator_email}")

            return creator_app_user

        except Exception as e:
            self.logger.error(f"Error setting up creator user: {e}")
            raise

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

    async def _find_parent_by_conversation_index_from_db(self, conversation_index: str, thread_id: str, org_id: str, user: AppUser) -> str | None:
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
                    connector_id=self.connector_id,
                    conversation_index=parent_index,
                    thread_id=thread_id,
                    org_id=org_id,
                    user_id=user.source_user_id
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

    async def _get_child_folders_recursive(
        self,
        parent_folder: dict
    ) -> list[dict]:
        """Recursively get all child folders of a parent folder for the authenticated user.

        Args:
            parent_folder: Parent folder dictionary

        Returns:
            Flattened list of all child folders (including nested children)
        """
        try:
            parent_folder_id = self._safe_get_attr(parent_folder, 'id')
            parent_folder_name = self._safe_get_attr(parent_folder, 'display_name', 'Unknown')

            if not parent_folder_id:
                return []

            # Check if folder has children
            child_folder_count = self._safe_get_attr(parent_folder, 'child_folder_count', 0)
            if child_folder_count == 0:
                self.logger.debug(f"Folder '{parent_folder_name}' has no child folders")
                return []

            # Fetch child folders using /me API (no user_id needed for delegated auth)
            if not self.external_outlook_client:
                raise Exception("External Outlook client not initialized")

            response: OutlookMailFoldersResponse = await self.external_outlook_client.me_mail_folders_list_child_folders(
                mailFolder_id=parent_folder_id
            )

            if not response.success:
                self.logger.warning(
                    f"Failed to get child folders for '{parent_folder_name}': {response.error}"
                )
                return []

            data = response.data or {}
            raw_child_folders = data.get('value', [])

            if not raw_child_folders:
                return []

            # Convert SDK objects to dicts
            child_folders = []
            for folder in raw_child_folders:
                if hasattr(folder, 'model_dump'):
                    folder_dict = folder.model_dump()
                elif hasattr(folder, 'dict'):
                    folder_dict = folder.dict()
                elif isinstance(folder, dict):
                    folder_dict = folder
                else:
                    folder_dict = {k: v for k, v in folder.__dict__.items() if not k.startswith('_')}
                child_folders.append(folder_dict)

            self.logger.info(
                f"Found {len(child_folders)} child folder(s) under '{parent_folder_name}'"
            )

            # Recursively process each child folder
            all_descendants = []
            for child in child_folders:
                all_descendants.append(child)
                # Recursively get grandchildren
                grandchildren = await self._get_child_folders_recursive(child)
                all_descendants.extend(grandchildren)

            return all_descendants

        except Exception as e:
            parent_name = self._safe_get_attr(parent_folder, 'display_name', 'Unknown')
            self.logger.error(f"Error getting child folders for '{parent_name}': {e}")
            return []

    def _is_descendant_of(
        self,
        folder_id: str,
        ancestor_ids: set[str],
        folder_parent_map: dict[str, str]
    ) -> bool:
        """Check if a folder is a descendant of any folder in ancestor_ids.

        Args:
            folder_id: The folder ID to check
            ancestor_ids: Set of potential ancestor folder IDs
            folder_parent_map: Map of folder_id -> parent_folder_id

        Returns:
            True if folder_id is a descendant of any folder in ancestor_ids
        """
        current_id = folder_id
        visited = set()

        while current_id in folder_parent_map and current_id not in visited:
            visited.add(current_id)
            parent_id = folder_parent_map[current_id]
            if parent_id in ancestor_ids:
                return True
            current_id = parent_id

        return False

    async def _get_all_folders_for_user(
        self,
        selected_folder_ids: list[str] | None = None,
        filter_operator: MultiselectOperator | None = None,
        display_name_filter: str | None = None
    ) -> list[dict]:
        """Get all folders for the authenticated user with optional filtering and nested folder support.

        Args:
            selected_folder_ids: Optional list of folder IDs to filter (client-side filtering)
            filter_operator: Filter operator (IN or NOT_IN)
            display_name_filter: Optional search term for server-side startsWith filtering on displayName

        Returns:
            List of folder dictionaries (includes nested folders by default)
        """
        try:
            if not self.external_outlook_client:
                raise Exception("External Outlook client not initialized")

            # Build OData filter for server-side search if provided
            odata_filter = None
            if display_name_filter:
                # Use startsWith for server-side filtering (contains is not supported on mailFolders)
                odata_filter = f"startsWith(displayName, '{display_name_filter}')"

            # Paginate through all top-level folders using cursor-based pagination
            top_level_folders = []
            next_url = None
            page_num = 1
            page_size = 50

            while True:
                # Get folders page using /me API (no user_id needed for delegated auth)
                if next_url:
                    response: OutlookMailFoldersResponse = await self.external_outlook_client.me_list_mail_folders(
                        next_url=next_url
                    )
                else:
                    response: OutlookMailFoldersResponse = await self.external_outlook_client.me_list_mail_folders(
                        includeHiddenFolders=None,
                        filter=odata_filter,
                        top=page_size
                    )

                if not response.success:
                    self.logger.error(f"Failed to get folders page {page_num}: {response.error}")
                    break

                data = response.data or {}
                raw_folders = data.get('value', [])

                # Convert SDK objects to dicts if needed
                for folder in raw_folders:
                    if hasattr(folder, 'model_dump'):
                        folder_dict = folder.model_dump()
                    elif hasattr(folder, 'dict'):
                        folder_dict = folder.dict()
                    elif isinstance(folder, dict):
                        folder_dict = folder
                    else:
                        folder_dict = {k: v for k, v in folder.__dict__.items() if not k.startswith('_')}
                    top_level_folders.append(folder_dict)

                # Check for next page
                next_url = self._safe_get_attr(data, '@odata.nextLink') or self._safe_get_attr(data, 'odata_next_link')
                if not next_url:
                    break

                page_num += 1

            self.logger.info(f"Retrieved {len(top_level_folders)} top-level folders across {page_num} page(s)")

            # Get all folders including nested
            all_folders = []
            for folder in top_level_folders:
                folder['_is_top_level'] = True
                all_folders.append(folder)
                # Recursively get child folders
                child_folders = await self._get_child_folders_recursive(folder)
                all_folders.extend(child_folders)

            total_nested = len(all_folders) - len(top_level_folders)
            if total_nested > 0:
                self.logger.info(
                    f"Total: {len(top_level_folders)} top-level + "
                    f"{total_nested} nested = {len(all_folders)} total folders"
                )
            else:
                self.logger.info(f"Total: {len(all_folders)} folders (no nested folders found)")

            # Build folder parent map for hierarchy traversal
            folder_parent_map = {}
            for folder in all_folders:
                folder_id = folder.get("id")
                parent_id = self._safe_get_attr(folder, 'parent_folder_id')
                if folder_id and parent_id:
                    folder_parent_map[folder_id] = parent_id

            # Apply client-side folder ID filtering if specified
            if selected_folder_ids and filter_operator:
                operator_str = filter_operator.value if hasattr(filter_operator, 'value') else str(filter_operator)

                if operator_str == MultiselectOperator.IN.value:
                    # Include selected folders AND all their descendants
                    selected_ids_set = set(selected_folder_ids)

                    filtered_folders = [
                        folder for folder in all_folders
                        if folder.get("id") in selected_ids_set or 
                           self._is_descendant_of(folder.get("id"), selected_ids_set, folder_parent_map)
                    ]

                    nested_count = len(filtered_folders) - len([f for f in filtered_folders if f.get("id") in selected_ids_set])
                    self.logger.info(
                        f"Including {len(filtered_folders)} folders "
                        f"({len(selected_folder_ids)} selected + {nested_count} nested) "
                        f"out of {len(all_folders)} total"
                    )
                elif operator_str == MultiselectOperator.NOT_IN.value:
                    # Exclude selected folders AND all their descendants
                    excluded_ids_set = set(selected_folder_ids)

                    filtered_folders = [
                        folder for folder in all_folders
                        if folder.get("id") not in excluded_ids_set and 
                           not self._is_descendant_of(folder.get("id"), excluded_ids_set, folder_parent_map)
                    ]

                    excluded_count = len(all_folders) - len(filtered_folders)
                    self.logger.info(
                        f"Excluding {excluded_count} folders (selected + descendants), "
                        f"{len(filtered_folders)} remaining out of {len(all_folders)} total"
                    )
                else:
                    # Unknown operator, return all folders
                    self.logger.warning(f"Unknown folder filter operator: {operator_str}, returning all folders")
                    return all_folders

                return filtered_folders

            return all_folders

        except Exception as e:
            self.logger.error(f"Error getting folders for authenticated user: {e}")
            return []

    def _transform_folder_to_record_group(
        self,
        folder: dict,
        user: AppUser
    ) -> RecordGroup | None:
        """
        Transform Outlook mail folder to RecordGroup entity.

        Args:
            folder: Raw folder data from Microsoft Graph API
            user: AppUser who owns this mailbox

        Returns:
            RecordGroup object or None if transformation fails
        """
        try:
            folder_id = self._safe_get_attr(folder, 'id')
            folder_name = self._safe_get_attr(folder, 'display_name', 'Unnamed Folder')

            if not folder_id:
                return None

            # Get parent folder ID for hierarchy
            # Top-level folders should not store parent_external_group_id even if API returns it
            is_top_level = self._safe_get_attr(folder, '_is_top_level', False)
            parent_folder_id = None if is_top_level else self._safe_get_attr(folder, 'parent_folder_id')

            # Create simple description
            description = f"{folder_name} folder for {user.email}"

            return RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=folder_name,
                short_name=folder_name,
                description=description,
                external_group_id=folder_id,
                parent_external_group_id=parent_folder_id,
                connector_name=Connectors.OUTLOOK_INDIVIDUAL,
                connector_id=self.connector_id,
                group_type=RecordGroupType.MAILBOX,
                web_url=None,
                source_created_at=None,
                source_updated_at=None,
            )

        except Exception as e:
            self.logger.error(f"Error transforming folder to RecordGroup: {e}")
            return None

    async def _sync_user_folders(self, user: AppUser) -> list[dict]:
        """
        Sync mail folders for the authenticated user as RecordGroup entities and return folder data.

        Args:
            user: AppUser whose folders to sync (used for DB operations, not API calls)

        Returns:
            List of folder data (for email processing)
        """
        try:
            # Get folder filter (IDs and operator)
            folders_filter = self.sync_filters.get(SyncFilterKey.FOLDERS)
            selected_folder_ids = None
            filter_operator = None

            if folders_filter and not folders_filter.is_empty():
                selected_folder_ids = folders_filter.get_value()
                if selected_folder_ids:
                    filter_operator = folders_filter.get_operator()
                    operator_str = filter_operator.value if hasattr(filter_operator, 'value') else str(filter_operator)
                    self.logger.info(f"Folder filter: {operator_str} with {len(selected_folder_ids)} folders")

            # Get folders for authenticated user with filtering applied
            folders = await self._get_all_folders_for_user(
                selected_folder_ids=selected_folder_ids,
                filter_operator=filter_operator
            )

            if not folders:
                self.logger.debug(f"No folders to sync for user {user.email} after filtering")
                return []

            # Transform folders to RecordGroups
            record_groups = []
            for folder in folders:
                record_group = self._transform_folder_to_record_group(folder, user)
                if record_group:
                    record_groups.append(record_group)

            self.logger.info(f"Syncing {len(record_groups)} folders for user {user.email}")

            # Sync to database with owner permission for mailbox owner
            if record_groups:
                self.logger.info(f"Syncing record groups: {record_groups}")
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

    async def _process_single_folder_messages(self, org_id: str, user: AppUser, folder: dict) -> tuple[int, list[Record]]:
        """Process messages using batch processing with automatic pagination and /me API."""
        try:
            folder_id = self._safe_get_attr(folder, 'id')
            folder_name = self._safe_get_attr(folder, 'display_name', 'Unnamed Folder')

            # Create folder-specific sync point (use folder_id only for personal connector)
            sync_point_key = generate_record_sync_point_key(
                RecordType.MAIL.value, "folders", folder_id
            )
            sync_point = await self.email_delta_sync_point.read_sync_point(sync_point_key)
            delta_link = sync_point.get('delta_link') if sync_point else None

            # Get messages for this folder using delta sync with /me API
            result = await self._get_all_messages_delta_external(folder_id, delta_link)
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

            await self.email_delta_sync_point.update_sync_point(
                sync_point_key,
                sync_point_data,
                encrypt_fields=['delta_link']
            )

            # Log final summary
            self.logger.info(f"Folder '{folder_name}' completed: {processed_count} records processed from {len(messages)} messages")

            return processed_count, mail_records

        except Exception as e:
            self.logger.error(f"Error processing messages in folder '{folder_name}' for user {user.email}: {e}")
            return 0, []

    async def _get_all_messages_delta_external(self, folder_id: str, delta_link: str | None = None) -> dict:
        """Get folder messages for authenticated user using delta sync with automatic pagination.

        This method handles both initial sync and incremental sync using /me API:
        - Initial sync (delta_link=None): Retrieves all messages in the folder
        - Incremental sync (delta_link provided): Retrieves only changes since last sync

        Pagination is handled automatically:
        - The method follows nextLink URLs to fetch all pages
        - Returns when deltaLink is received (signals completion)
        - Maximum page size is 100 messages per request

        Args:
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
            received_before_dt: datetime | None = None  # For client-side filtering

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

            # Use helper method for /me delta sync with automatic pagination
            select_fields = [
                'id', 'subject', 'hasAttachments', 'createdDateTime',
                'lastModifiedDateTime', 'receivedDateTime', 'webLink',
                'from', 'toRecipients', 'ccRecipients', 'bccRecipients',
                'conversationId', 'internetMessageId', 'conversationIndex'
            ]

            raw_messages, new_delta_link = await self.external_outlook_client.fetch_all_messages_delta_me(
                mailFolder_id=folder_id,
                saved_delta_link=delta_link,
                page_size=100,
                select=select_fields,
                filter=filter_string
            )

            # Convert SDK objects to dicts
            messages = []
            for msg in raw_messages:
                if hasattr(msg, 'model_dump'):
                    msg_dict = msg.model_dump()
                elif hasattr(msg, 'dict'):
                    msg_dict = msg.dict()
                elif isinstance(msg, dict):
                    msg_dict = msg
                else:
                    msg_dict = {k: v for k, v in msg.__dict__.items() if not k.startswith('_')}
                messages.append(msg_dict)

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

    async def _process_single_message(
        self, org_id: str, user: AppUser, message: dict[str, object], folder_id: str, folder_name: str
    ) -> list[RecordUpdate]:
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
                    await tx_store.delete_record_by_external_id(self.connector_id, message_id, user.source_user_id)
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
        self,
        org_id: str,
        user_email: str,
        message: dict[str, object],
        folder_id: str,
        folder_name: str,
        existing_record: Record | None = None,
    ) -> RecordUpdate | None:
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
                connector_name=Connectors.OUTLOOK_INDIVIDUAL,
                connector_id=self.connector_id,
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
            self.logger.error(f"Error processing email {self._safe_get_attr(message, 'id', 'unknown')}: {str(e)}")
            return None

    async def _extract_email_permissions(self, message: dict, record_id: str | None, inbox_owner_email: str) -> list[Permission]:
        """Extract permission for inbox owner only (personal mailbox).

        For personal connector, only the mailbox owner gets OWNER permission.
        All emails in personal mailbox are owned by the creator.
        """
        try:
            # Personal connector: only creator has access to their mailbox
            return [Permission(
                email=inbox_owner_email,
                type=PermissionType.OWNER,
                entity_type=EntityType.USER,
            )]

        except Exception as e:
            self.logger.error(f"Error extracting permission: {e}")
            return []

    async def _create_attachment_record(
        self,
        org_id: str,
        attachment: dict,
        message_id: str,
        folder_id: str,
        existing_record: Record | None = None,
        parent_weburl: str | None = None,
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
            FileRecord: Created attachment record, or None if attachment should be skipped
        """
        attachment_id = self._safe_get_attr(attachment, 'id')
        is_new = existing_record is None

        # Check if content_type is available, skip attachment if not
        content_type = self._safe_get_attr(attachment, 'content_type')
        if not content_type:
            file_name = self._safe_get_attr(attachment, 'name', 'Unknown')
            self.logger.warning(f"Skipping attachment '{file_name}' (id: {attachment_id}) - no content_type available")
            return None

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
            connector_name=Connectors.OUTLOOK_INDIVIDUAL,
            connector_id=self.connector_id,
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
            attachment_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

        return attachment_record

    async def _process_email_attachments_with_folder(self, org_id: str, user: AppUser, message: dict,
                                                  email_permissions: list[Permission], folder_id: str, folder_name: str) -> list[RecordUpdate]:
        """Process email attachments with folder information."""
        attachment_updates = []

        try:
            message_id = self._safe_get_attr(message, 'id')
            parent_weburl = self._safe_get_attr(message, 'web_link')

            attachments = await self._get_message_attachments_external(message_id)

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
            self.logger.error(f"Error processing attachments for email {self._safe_get_attr(message, 'id', 'unknown')}: {e}")
            return []

    async def _get_message_attachments_external(self, message_id: str) -> list[dict]:
        """Get message attachments for authenticated user using /me API."""
        try:
            if not self.external_outlook_client:
                raise Exception("External Outlook client not initialized")

            response: OutlookCalendarContactsResponse = await self.external_outlook_client.me_messages_list_attachments(
                message_id=message_id
            )

            if not response.success:
                self.logger.error(f"Failed to get attachments for message {message_id}: {response.error}")
                return []

            # Convert SDK objects to dicts
            raw_attachments = self._safe_get_attr(response.data, 'value', [])
            attachments = []
            for att in raw_attachments:
                if hasattr(att, 'model_dump'):
                    att_dict = att.model_dump()
                elif hasattr(att, 'dict'):
                    att_dict = att.dict()
                elif isinstance(att, dict):
                    att_dict = att
                else:
                    att_dict = {k: v for k, v in att.__dict__.items() if not k.startswith('_')}
                attachments.append(att_dict)

            return attachments

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
        """Stream record content (email or attachment) using /me API."""
        try:
            if not self.external_outlook_client:
                raise HTTPException(status_code=500, detail="External Outlook client not initialized")

            if record.record_type == RecordType.MAIL:
                # User email using /me API
                message = await self._get_message_by_id_external(record.external_record_id)
                body_obj = self._safe_get_attr(message, 'body')
                email_body = self._safe_get_attr(body_obj, 'content', '') if body_obj else ''
                # Augment with recipient metadata for indexing
                if isinstance(record, MailRecord):
                    email_body = self._augment_email_html_with_metadata(email_body, record)
                async def generate_email() -> AsyncGenerator[bytes, None]:
                    yield email_body.encode('utf-8')

                return StreamingResponse(generate_email(), media_type='text/html')

            elif record.record_type == RecordType.FILE:
                # User email attachment using /me API
                attachment_id = record.external_record_id
                parent_message_id = record.parent_external_record_id

                if not parent_message_id:
                    raise HTTPException(status_code=404, detail="No parent message ID stored for attachment")

                attachment_data = await self._download_attachment_external(parent_message_id, attachment_id)

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
                raise HTTPException(status_code=400, detail="Unsupported record type for streaming")

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to stream record: {str(e)}") from e

    async def _get_message_by_id_external(self, message_id: str) -> dict:
        """Get a specific message by ID for authenticated user using /me API."""
        try:
            if not self.external_outlook_client:
                raise Exception("External Outlook client not initialized")

            response: OutlookCalendarContactsResponse = await self.external_outlook_client.me_get_message(
                message_id=message_id
            )

            if not response.success:
                self.logger.error(f"Failed to get message {message_id}: {response.error}")
                return {}

            # Convert SDK object to dict
            message_data = response.data
            if not message_data:
                return {}

            if hasattr(message_data, 'model_dump'):
                return message_data.model_dump()
            elif hasattr(message_data, 'dict'):
                return message_data.dict()
            elif isinstance(message_data, dict):
                return message_data
            else:
                return {k: v for k, v in message_data.__dict__.items() if not k.startswith('_')}

        except Exception as e:
            self.logger.error(f"Error getting message {message_id}: {e}")
            return {}

    async def _download_attachment_external(self, message_id: str, attachment_id: str) -> bytes:
        """Download attachment content for authenticated user using /me API."""
        try:
            if not self.external_outlook_client:
                raise Exception("External Outlook client not initialized")

            response: OutlookCalendarContactsResponse = await self.external_outlook_client.me_messages_get_attachments(
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
            self.credentials = None
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

            # Ensure external client is initialized
            if not self.external_outlook_client:
                self.logger.error("External Outlook client not initialized. Call init() first.")
                raise Exception("External Outlook client not initialized. Call init() first.")

            # Process all records (personal connector only has user mailbox records)
            updated_records_with_permissions, non_updated_records = await self._reindex_user_mailbox_records(records)

            # Update DB and publish events for updated records
            if updated_records_with_permissions:
                await self.data_entities_processor.on_new_records(updated_records_with_permissions)
                self.logger.info(f"Updated {len(updated_records_with_permissions)} records in DB that changed at source")

            # Publish reindex events for non-updated records
            if non_updated_records:
                await self.data_entities_processor.reindex_existing_records(non_updated_records)
                self.logger.info(f"Published reindex events for {len(non_updated_records)} non-updated records")

            self.logger.info(f"Outlook reindex completed for {len(records)} records")

        except Exception as e:
            self.logger.error(f"Error during Outlook reindex: {e}")
            raise

    async def _reindex_user_mailbox_records(
        self, records: list[Record]
    ) -> tuple[list[tuple[Record, list[Permission]]], list[Record]]:
        """Reindex user mailbox records for creator. Checks source for updates using /me API.

        Returns:
            Tuple of (updated_records_with_permissions, non_updated_records)
        """
        if not records:
            return ([], [])

        updated_records_with_permissions: list[tuple[Record, list[Permission]]] = []
        non_updated_records: list[Record] = []

        try:
            self.logger.info(f"Checking {len(records)} records at source for creator {self.creator_email}")

            org_id = self.data_entities_processor.org_id

            for record in records:
                try:
                    updated_record_data = await self._check_and_fetch_updated_record(
                        org_id, self.creator_email, record
                    )
                    if updated_record_data:
                        updated_record, permissions = updated_record_data
                        updated_records_with_permissions.append((updated_record, permissions))
                    else:
                        non_updated_records.append(record)
                except Exception as e:
                    self.logger.error(f"Error checking record {record.id} at source: {e}")
                    continue

            self.logger.info(f"Completed source check: {len(updated_records_with_permissions)} updated, {len(non_updated_records)} unchanged")

        except Exception as e:
            self.logger.error(f"Error reindexing records: {e}")
            raise

        return (updated_records_with_permissions, non_updated_records)

    async def _check_and_fetch_updated_record(
        self, org_id: str, user_email: str, record: Record
    ) -> tuple[Record, list[Permission]] | None:
        """Fetch record from source and return data for reindexing using /me API.

        Args:
            org_id: Organization ID
            user_email: User email for permission extraction
            record: Record to check

        Returns:
            Tuple of (Record, List[Permission]) for processing via on_new_records
        """
        try:
            if record.record_type == RecordType.MAIL:
                return await self._check_and_fetch_updated_email(org_id, user_email, record)
            elif record.record_type == RecordType.FILE:
                return await self._check_and_fetch_updated_attachment(org_id, user_email, record)
            else:
                self.logger.warning(f"Unsupported record type for reindex: {record.record_type}")
                return None

        except Exception as e:
            self.logger.error(f"Error checking record {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_email(
        self, org_id: str, user_email: str, record: Record
    ) -> tuple[Record, list[Permission]] | None:
        """Fetch email from source for reindexing using /me API."""
        try:
            message_id = record.external_record_id

            message = await self._get_message_by_id_external(message_id)
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
        self, org_id: str, user_email: str, record: Record
    ) -> tuple[Record, list[Permission]] | None:
        """Fetch attachment from source for reindexing using /me API."""
        try:
            attachment_id = record.external_record_id
            parent_message_id = record.parent_external_record_id

            if not parent_message_id:
                self.logger.warning(f"Attachment {attachment_id} has no parent message ID")
                return None

            message = await self._get_message_by_id_external(parent_message_id)
            if not message:
                self.logger.warning(f"Parent message {parent_message_id} not found at source")
                return None

            attachments = await self._get_message_attachments_external(parent_message_id)

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

            # Return None if attachment was filtered out
            if not attachment_record:
                return None

            return (attachment_record, email_permissions)

        except Exception as e:
            self.logger.error(f"Error fetching attachment {record.external_record_id}: {e}")
            return None


    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: str | None = None,
        cursor: str | None = None
    ) -> FilterOptionsResponse:
        """Get dynamic filter options for the folders filter."""
        if filter_key == SyncFilterKey.FOLDERS.value:
            return await self._get_folder_options(page, limit, search, cursor)
        raise ValueError(f"Unsupported filter key: {filter_key}")

    async def _get_folder_options(
        self,
        page: int,
        limit: int,
        search: str | None,
        cursor: str | None = None
    ) -> FilterOptionsResponse:
        """Get list of available mail folders for the creator using API-level pagination.

        Uses cursor-based pagination with $top parameter. No $skip support.
        Page 1: calls API with top=limit
        Page 2+: uses cursor from previous response's @odata.nextLink
        """
        try:
            if not self.external_outlook_client:
                return FilterOptionsResponse(
                    success=False,
                    options=[],
                    page=page,
                    limit=limit,
                    has_more=False,
                    message="Outlook connector is not initialized"
                )

            if not self.creator_email:
                return FilterOptionsResponse(
                    success=False,
                    options=[],
                    page=page,
                    limit=limit,
                    has_more=False,
                    message="Creator email not available"
                )

            # Cap limit to max 100
            cap = max(1, min(limit, 100))

            # Build OData filter for server-side search if provided
            odata_filter = None
            if search:
                search_term = search.strip()
                # Use startsWith for server-side filtering (contains is not supported on mailFolders)
                odata_filter = f"startsWith(displayName, '{search_term}')"

            # Fetch single page using API-level pagination
            if cursor:
                # Use cursor for page 2+
                response: OutlookMailFoldersResponse = await self.external_outlook_client.me_list_mail_folders(
                    next_url=cursor
                )
            else:
                # Page 1: use top parameter
                response: OutlookMailFoldersResponse = await self.external_outlook_client.me_list_mail_folders(
                    includeHiddenFolders=None,
                    filter=odata_filter,
                    top=cap
                )

            if not response.success:
                return FilterOptionsResponse(
                    success=False,
                    options=[],
                    page=page,
                    limit=cap,
                    has_more=False,
                    message=response.error or "Failed to fetch folders"
                )

            data = response.data or {}
            raw_folders = data.get('value', [])

            # Build folder options from this page only
            folder_options = []
            for folder in raw_folders:
                # Handle both dict and SDK object
                if hasattr(folder, 'model_dump'):
                    folder_dict = folder.model_dump()
                elif hasattr(folder, 'dict'):
                    folder_dict = folder.dict()
                elif isinstance(folder, dict):
                    folder_dict = folder
                else:
                    folder_dict = {k: v for k, v in folder.__dict__.items() if not k.startswith('_')}

                folder_id = folder_dict.get("id")
                folder_name = folder_dict.get("display_name") or folder_dict.get("displayName", "Unknown Folder")
                if folder_id:
                    folder_options.append(FilterOption(id=folder_id, label=folder_name))

            # Extract next cursor from @odata.nextLink
            next_cursor = self._safe_get_attr(data, '@odata.nextLink') or self._safe_get_attr(data, 'odata_next_link')
            has_more = next_cursor is not None

            return FilterOptionsResponse(
                success=True,
                options=folder_options,
                page=page,
                limit=cap,
                has_more=has_more,
                cursor=next_cursor
            )

        except Exception as e:
            self.logger.error(f"Failed to get folder options: {e}")
            return FilterOptionsResponse(
                success=False,
                options=[],
                page=page,
                limit=limit,
                has_more=False,
                message=f"Error fetching folders: {str(e)}"
            )

    def _extract_email_from_recipient(self, recipient: object) -> str:
        """Extract email address from a Recipient object."""
        if not recipient:
            return ''

        # Handle Recipient objects with emailAddress property
        email_addr = self._safe_get_attr(recipient, 'email_address') or self._safe_get_attr(recipient, 'emailAddress')
        if email_addr:
            return self._safe_get_attr(email_addr, 'address', '')

        # Fallback to string representation
        return str(recipient) if recipient else ''

    def _safe_get_attr(self, obj: object, attr_name: str, default: object | None = None) -> object | None:
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

    def _parse_datetime(self, dt_obj: datetime | str | None) -> int | None:
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

    @classmethod
    async def create_connector(cls, logger: Logger, data_store_provider: DataStoreProvider, config_service: ConfigurationService, connector_id: str) -> 'OutlookIndividualConnector':
        """Factory method to create and initialize OutlookIndividualConnector."""
        data_entities_processor = DataSourceEntitiesProcessor(logger, data_store_provider, config_service)
        await data_entities_processor.initialize()

        return OutlookIndividualConnector(logger, data_entities_processor, data_store_provider, config_service, connector_id)
