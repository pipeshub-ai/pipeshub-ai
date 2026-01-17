import logging
from logging import Logger
from typing import Dict, List, Optional

from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
)
from app.connectors.core.registry.auth_builder import AuthType, OAuthScopeConfig
from app.connectors.core.registry.connector_builder import (
    AuthBuilder,
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
)
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOptionsResponse,
    load_connector_filters,
)
from app.connectors.sources.google.common.apps import GmailApp
from app.models.entities import AppUser, AppUserGroup, Record, RecordGroup, RecordGroupType
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.google.google import GoogleClient
from app.sources.external.google.admin.admin import GoogleAdminDataSource
from app.sources.external.google.gmail.gmail import GoogleGmailDataSource
from app.utils.time_conversion import parse_timestamp


@ConnectorBuilder("Gmail Workspace")\
    .in_group("Google Workspace")\
    .with_description("Sync emails and messages from Gmail")\
    .with_categories(["Email"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Gmail Workspace",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            redirect_uri="connectors/oauth/callback/Gmail",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[
                    "https://www.googleapis.com/auth/gmail.readonly",
                    "https://www.googleapis.com/auth/gmail.metadata",
                ],
                agent=[]
            ),
            fields=[
                CommonFields.client_id("Google Cloud Console"),
                CommonFields.client_secret("Google Cloud Console")
            ],
            icon_path="/assets/icons/connectors/gmail.svg",
            app_group="Google Workspace",
            app_description="OAuth application for accessing Gmail API and related Google Workspace services",
            app_categories=["Email"],
            additional_params={
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true"
            }
        )
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/gmail.svg")
        .with_realtime_support(True)
        .add_documentation_link(DocumentationLink(
            "Gmail API Setup",
            "https://developers.google.com/workspace/guides/auth-overview",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/google-workspace/gmail/gmail',
            'pipeshub'
        ))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .with_webhook_config(False, [])
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .add_sync_custom_field(CommonFields.batch_size_field())
        .with_sync_support(True)
        .with_agent_support(True)
    )\
    .build_decorator()
class GoogleGmailTeamConnector(BaseConnector):
    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str
    ) -> None:
        super().__init__(
            GmailApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id
        )

        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider
            )

        # Initialize sync points
        self.gmail_delta_sync_point = _create_sync_point(SyncDataPointType.RECORDS)
        self.user_sync_point = _create_sync_point(SyncDataPointType.USERS)
        self.user_group_sync_point = _create_sync_point(SyncDataPointType.GROUPS)
        self.connector_id = connector_id

        # Batch processing configuration
        self.batch_size = 100
        self.max_concurrent_batches = 3

        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

        # Google clients and data sources (initialized in init())
        self.admin_client: Optional[GoogleClient] = None
        self.gmail_client: Optional[GoogleClient] = None
        self.admin_data_source: Optional[GoogleAdminDataSource] = None
        self.gmail_data_source: Optional[GoogleGmailDataSource] = None
        self.config: Optional[Dict] = None
        logging.getLogger('googleapiclient.http').setLevel(logging.ERROR)

        # Store synced users for use in batch processing
        self.synced_users: List[AppUser] = []

    async def init(self) -> bool:
        """Initialize the Google Gmail enterprise connector with service account credentials and services."""
        try:
            # Load connector config
            config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config"
            )
            if not config:
                self.logger.error("Google Gmail enterprise config not found")
                return False

            self.config = {"credentials": config}

            # Extract service account credentials JSON from config
            # GoogleClient.build_from_services expects credentials in 'auth' field
            credentials_json = config.get("auth", {})
            if not credentials_json:
                self.logger.error(
                    "Service account credentials not found in config. Ensure credentials JSON is configured under 'auth' field."
                )
                raise ValueError(
                    "Service account credentials not found. Ensure credentials JSON is configured under 'auth' field."
                )

            # Extract admin email from credentials JSON
            admin_email = credentials_json.get("adminEmail")
            if not admin_email:
                self.logger.error(
                    "Admin email not found in credentials. Ensure adminEmail is set in credentials JSON."
                )
                raise ValueError(
                    "Admin email not found in credentials. Ensure adminEmail is set in credentials JSON."
                )

            # Initialize Google Admin Client using build_from_services
            try:
                self.admin_client = await GoogleClient.build_from_services(
                    service_name="admin",
                    logger=self.logger,
                    config_service=self.config_service,
                    is_individual=False,  # This is an enterprise connector
                    version="directory_v1",
                    connector_instance_id=self.connector_id
                )

                # Create Google Admin Data Source from the client
                self.admin_data_source = GoogleAdminDataSource(
                    self.admin_client.get_client()
                )

                self.logger.info(
                    "✅ Google Admin client and data source initialized successfully"
                )
            except Exception as e:
                self.logger.error(
                    f"❌ Failed to initialize Google Admin client: {e}",
                    exc_info=True
                )
                raise ValueError(f"Failed to initialize Google Admin client: {e}") from e

            # Initialize Google Gmail Client using build_from_services
            try:
                self.gmail_client = await GoogleClient.build_from_services(
                    service_name="gmail",
                    logger=self.logger,
                    config_service=self.config_service,
                    is_individual=False,  # This is an enterprise connector
                    version="v1",
                    connector_instance_id=self.connector_id
                )

                # Create Google Gmail Data Source from the client
                self.gmail_data_source = GoogleGmailDataSource(
                    self.gmail_client.get_client()
                )

                self.logger.info(
                    "✅ Google Gmail client and data source initialized successfully"
                )
            except Exception as e:
                self.logger.error(
                    f"❌ Failed to initialize Google Gmail client: {e}",
                    exc_info=True
                )
                raise ValueError(f"Failed to initialize Google Gmail client: {e}") from e

            self.logger.info("✅ Google Gmail enterprise connector initialized successfully")
            return True

        except Exception as ex:
            self.logger.error(f"❌ Error initializing Google Gmail enterprise connector: {ex}", exc_info=True)
            raise

    async def _process_users_in_batches(self, users: List[AppUser]) -> None:
        pass

    async def run_sync(self) -> None:
        """
        Main entry point for the Google Gmail enterprise connector.
        Implements enterprise sync workflow: users → groups → record groups → process batches.
        """
        try:
            self.logger.info("Starting Google Gmail enterprise connector sync")

            # Load sync and indexing filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "gmail", self.connector_id, self.logger
            )

            # Step 1: Sync users
            self.logger.info("Syncing users...")
            await self._sync_users()

            # Step 2: Sync user groups and their members
            self.logger.info("Syncing user groups...")
            await self._sync_user_groups()

            # Step 3: Sync record groups for users
            self.logger.info("Syncing record groups...")
            await self._sync_record_groups(self.synced_users)

            # Step 4: Process user mailboxes in batches
            self.logger.info("Processing user mailboxes in batches...")
            # Use users synced in Step 1
            await self._process_users_in_batches(self.synced_users)

            self.logger.info("Google Gmail enterprise connector sync completed successfully")

        except Exception as e:
            self.logger.error(f"❌ Error in Google Gmail enterprise connector run: {e}", exc_info=True)
            raise

    async def _sync_users(self) -> None:
        """Sync all users from Google Workspace Admin API."""
        try:
            if not self.admin_data_source:
                self.logger.error("Admin data source not initialized. Call init() first.")
                raise ValueError("Admin data source not initialized")

            self.logger.info("Fetching all users from Google Workspace Admin API...")
            all_users: List[AppUser] = []
            page_token: Optional[str] = None

            while True:
                try:
                    # Fetch users with pagination
                    result = await self.admin_data_source.users_list(
                        customer="my_customer",
                        projection="full",
                        orderBy="email",
                        pageToken=page_token,
                        maxResults=500  # Maximum allowed by Google Admin API
                    )

                    users_data = result.get("users", [])
                    if not users_data:
                        break

                    # Transform Google user dictionaries to AppUser objects
                    for user in users_data:
                        try:
                            # Get email
                            email = user.get("primaryEmail") or user.get("email", "")
                            if not email:
                                self.logger.warning(f"Skipping user without email: {user.get('id')}")
                                continue

                            # Get full name
                            name_info = user.get("name", {})
                            full_name = name_info.get("fullName", "")
                            if not full_name:
                                given_name = name_info.get("givenName", "")
                                family_name = name_info.get("familyName", "")
                                full_name = f"{given_name} {family_name}".strip()
                            if not full_name:
                                full_name = email  # Fallback to email if no name available

                            # Get title from organizations
                            title = None
                            organizations = user.get("organizations", [])
                            if organizations and len(organizations) > 0:
                                title = organizations[0].get("title")

                            # Check if user is active (not suspended)
                            is_active = not user.get("suspended", False)

                            # Convert creation time to epoch milliseconds
                            source_created_at = None
                            creation_time = user.get("creationTime")
                            if creation_time:
                                try:
                                    source_created_at = parse_timestamp(creation_time)
                                except Exception as e:
                                    self.logger.warning(f"Failed to parse creation time for user {email}: {e}")

                            app_user = AppUser(
                                app_name=self.connector_name,
                                connector_id=self.connector_id,
                                source_user_id=user.get("id", ""),
                                email=email,
                                full_name=full_name,
                                is_active=is_active,
                                title=title,
                                source_created_at=source_created_at
                            )
                            all_users.append(app_user)

                        except Exception as e:
                            self.logger.error(f"Error processing user {user.get('id', 'unknown')}: {e}", exc_info=True)
                            continue

                    # Check for next page
                    page_token = result.get("nextPageToken")
                    if not page_token:
                        break

                    self.logger.info(f"Fetched {len(users_data)} users (total so far: {len(all_users)})")

                except Exception as e:
                    self.logger.error(f"Error fetching users page: {e}", exc_info=True)
                    raise

            if not all_users:
                self.logger.warning("No users found in Google Workspace")
                self.synced_users = []
                return

            # Process all users through the data entities processor
            self.logger.info(f"Processing {len(all_users)} users...")
            await self.data_entities_processor.on_new_app_users(all_users)

            # Store users for use in batch processing
            self.synced_users = all_users

            self.logger.info(f"✅ Successfully synced {len(all_users)} users")

        except Exception as e:
            self.logger.error(f"❌ Error syncing users: {e}", exc_info=True)
            raise

    async def _sync_user_groups(self) -> None:
        """Sync user groups and their members from Google Workspace Admin API."""
        try:
            if not self.admin_data_source:
                self.logger.error("Admin data source not initialized. Call init() first.")
                raise ValueError("Admin data source not initialized")

            self.logger.info("Fetching all groups from Google Workspace Admin API...")
            page_token: Optional[str] = None
            total_groups_processed = 0

            while True:
                try:
                    # Fetch groups with pagination
                    result = await self.admin_data_source.groups_list(
                        customer="my_customer",
                        orderBy="email",
                        pageToken=page_token,
                        maxResults=200  # Maximum allowed by Google Admin API
                    )

                    groups_data = result.get("groups", [])
                    if not groups_data:
                        break

                    # Process each group
                    for group in groups_data:
                        try:
                            await self._process_group(group)
                            total_groups_processed += 1
                        except Exception as e:
                            self.logger.error(
                                f"Error processing group {group.get('id', 'unknown')}: {e}",
                                exc_info=True
                            )
                            continue

                    # Check for next page
                    page_token = result.get("nextPageToken")
                    if not page_token:
                        break

                    self.logger.info(f"Processed {len(groups_data)} groups (total so far: {total_groups_processed})")

                except Exception as e:
                    self.logger.error(f"Error fetching groups page: {e}", exc_info=True)
                    raise

            self.logger.info(f"✅ Successfully synced {total_groups_processed} user groups")

        except Exception as e:
            self.logger.error(f"❌ Error syncing user groups: {e}", exc_info=True)
            raise

    async def _process_group(self, group: Dict) -> None:
        """
        Process a single group: fetch members and create AppUserGroup with AppUser objects.

        Args:
            group: Group dictionary from Google Admin API
        """
        try:
            group_id = group.get("email")
            if not group_id:
                self.logger.warning("Skipping group without ID")
                return

            # Fetch members for this group
            self.logger.debug(f"Fetching members for group: {group.get('name', group_id)}")
            members = await self._fetch_group_members(group_id)

            # Filter to only include user members (skip groups and customers)
            user_members = [m for m in members if m.get("type") == "USER"]

            # Create AppUserGroup object
            group_name = group.get("name", "")
            if not group_name:
                group_name = group.get("email", group_id)

            # Convert creation time to epoch milliseconds
            source_created_at = None
            creation_time = group.get("creationTime")
            if creation_time:
                try:
                    source_created_at = parse_timestamp(creation_time)
                except Exception as e:
                    self.logger.warning(f"Failed to parse creation time for group {group_id}: {e}")

            user_group = AppUserGroup(
                source_user_group_id=group_id,
                app_name=self.connector_name,
                connector_id=self.connector_id,
                name=group_name,
                description=group.get("description"),
                source_created_at=source_created_at
            )

            # Create AppUser objects for each member
            app_users: List[AppUser] = []
            for member in user_members:
                try:
                    member_email = member.get("email", "")
                    if not member_email:
                        self.logger.warning(f"Skipping member without email: {member.get('id')}")
                        continue

                    # For group members, we may not have full user details
                    # Use email as fallback for full_name if not available
                    member_id = member.get("id", "")

                    # Try to find user in synced users list for full details
                    full_name = member_email  # Default to email
                    source_created_at_user = None

                    # Look up user in synced users if available
                    if self.synced_users:
                        for synced_user in self.synced_users:
                            if synced_user.source_user_id == member_id or synced_user.email.lower() == member_email.lower():
                                full_name = synced_user.full_name
                                source_created_at_user = synced_user.source_created_at
                                break

                    app_user = AppUser(
                        app_name=self.connector_name,
                        connector_id=self.connector_id,
                        source_user_id=member_id,
                        email=member_email,
                        full_name=full_name,
                        source_created_at=source_created_at_user
                    )
                    app_users.append(app_user)

                except Exception as e:
                    self.logger.error(f"Error processing group member {member.get('id', 'unknown')}: {e}", exc_info=True)
                    continue

            # Send to processor
            if app_users:
                await self.data_entities_processor.on_new_user_groups([(user_group, app_users)])
                self.logger.debug(f"Processed group '{group_name}' with {len(app_users)} members")
            else:
                self.logger.debug(f"Group '{group_name}' has no user members, skipping")

        except Exception as e:
            self.logger.error(f"Error processing group {group.get('id', 'unknown')}: {e}", exc_info=True)
            raise

    async def _fetch_group_members(self, group_id: str) -> List[Dict]:
        """
        Fetch all members of a group with pagination.

        Args:
            group_id: The group ID or email

        Returns:
            List of member dictionaries
        """
        members: List[Dict] = []
        page_token: Optional[str] = None

        while True:
            try:
                result = await self.admin_data_source.members_list(
                    groupKey=group_id,
                    pageToken=page_token,
                    maxResults=200  # Maximum allowed by Google Admin API
                )

                page_members = result.get("members", [])
                if not page_members:
                    break

                members.extend(page_members)

                # Check for next page
                page_token = result.get("nextPageToken")
                if not page_token:
                    break

            except Exception as e:
                self.logger.error(f"Error fetching members for group {group_id}: {e}", exc_info=True)
                raise

        return members
        
    async def _sync_record_groups(self, users: List[AppUser]) -> None:
        """Sync record groups (labels) for users.
        
        For each user, fetches their Gmail labels and creates record groups
        with owner permissions from the user to each label record group.
        
        Args:
            users: List of AppUser objects to sync labels for
        """
        try:
            if not users:
                self.logger.warning("No users provided for record group sync")
                return

            self.logger.info(f"Syncing record groups (labels) for {len(users)} users...")
            total_labels_processed = 0

            for user in users:
                user_gmail_client = None
                user_gmail_data_source = None
                try:
                    if not user.email:
                        self.logger.warning(f"Skipping user without email: {user.source_user_id}")
                        continue

                    self.logger.debug(f"Fetching labels for user: {user.email}")

                    # Create user-specific Gmail client with impersonation
                    user_gmail_client = await GoogleClient.build_from_services(
                        service_name="gmail",
                        logger=self.logger,
                        config_service=self.config_service,
                        is_individual=False,  # Enterprise connector
                        version="v1",
                        user_email=user.email,  # Impersonate this user
                        connector_instance_id=self.connector_id
                    )

                    user_gmail_data_source = GoogleGmailDataSource(
                        user_gmail_client.get_client()
                    )

                    # Fetch labels for this user
                    labels_response = await user_gmail_data_source.users_labels_list(
                        userId=user.email
                    )

                    labels = labels_response.get("labels", [])
                    if not labels:
                        self.logger.debug(f"No labels found for user {user.email}")
                        continue

                    self.logger.info(
                        f"Found {len(labels)} labels for user {user.email}"
                    )

                    # Create record groups for each label
                    for label in labels:
                        try:
                            label_id = label.get("id", "")
                            label_name = label.get("name", "Unnamed Label")

                            if not label_id:
                                self.logger.warning(
                                    f"Skipping label without ID for user {user.email}: {label_name}"
                                )
                                continue

                            # Create record group name: "{user.full_name} - {label.name}"
                            record_group_name = f"{user.full_name} - {label_name}"

                            # Create external_group_id: "{user.email} - {label.id}"
                            external_group_id = f"{user.email}:{label_id}"

                            # Create record group
                            record_group = RecordGroup(
                                name=record_group_name,
                                org_id=self.data_entities_processor.org_id,
                                external_group_id=external_group_id,
                                description=f"Gmail label: {label_name}",
                                connector_name=self.connector_name,
                                connector_id=self.connector_id,
                                group_type=RecordGroupType.MAILBOX,
                                source_created_at=user.source_created_at
                            )

                            # Create owner permission from user to record group
                            owner_permission = Permission(
                                email=user.email,
                                type=PermissionType.OWNER,
                                entity_type=EntityType.USER
                            )

                            # Submit to processor
                            await self.data_entities_processor.on_new_record_groups(
                                [(record_group, [owner_permission])]
                            )

                            total_labels_processed += 1
                            self.logger.debug(
                                f"Created record group '{record_group_name}' for user {user.email}"
                            )

                        except Exception as e:
                            self.logger.error(
                                f"Error creating record group for label '{label.get('name', 'unknown')}' "
                                f"for user {user.email}: {e}",
                                exc_info=True
                            )
                            continue

                except Exception as e:
                    self.logger.error(
                        f"Error processing labels for user {user.email}: {e}",
                        exc_info=True
                    )
                    continue
                finally:
                    # Cleanup user-specific client resources
                    try:
                        if user_gmail_data_source and hasattr(user_gmail_data_source, 'client'):
                            client = user_gmail_data_source.client
                            if hasattr(client, 'close'):
                                client.close()
                    except Exception as cleanup_error:
                        self.logger.debug(f"Error during client cleanup for {user.email}: {cleanup_error}")
                    
                    # Clear references to help with garbage collection
                    user_gmail_data_source = None
                    user_gmail_client = None

            self.logger.info(
                f"✅ Successfully synced {total_labels_processed} label record groups "
                f"for {len(users)} users"
            )

        except Exception as e:
            self.logger.error(f"❌ Error syncing record groups: {e}", exc_info=True)
            raise

    async def test_connection_and_access(self) -> bool:
        """Test connection and access to Google Gmail enterprise account."""
        try:
            self.logger.info("Testing connection and access to Google Gmail enterprise account")
            if not self.gmail_data_source:
                self.logger.error("Gmail data source not initialized. Call init() first.")
                return False

            if not self.admin_data_source:
                self.logger.error("Admin data source not initialized. Call init() first.")
                return False

            if not self.gmail_client or not self.admin_client:
                self.logger.warning("Google clients not initialized")
                return False

            return True
        except Exception as e:
            self.logger.error(f"❌ Error testing connection and access to Google Gmail enterprise account: {e}")
            return False

    def get_signed_url(self, record: Record) -> Optional[str]:
        """Get a signed URL for a specific record."""
        raise NotImplementedError("get_signed_url is not yet implemented for Google Gmail enterprise")

    async def stream_record(self, record: Record, user_id: Optional[str] = None, convertTo: Optional[str] = None) -> StreamingResponse:
        """Stream a record from Google Gmail."""
        raise NotImplementedError("stream_record is not yet implemented for Google Gmail enterprise")

    async def run_incremental_sync(self) -> None:
        """Run incremental sync for Google Gmail enterprise."""
        raise NotImplementedError("run_incremental_sync is not yet implemented for Google Gmail enterprise")

    def handle_webhook_notification(self, notification: Dict) -> None:
        """Handle webhook notifications from Google Gmail."""
        raise NotImplementedError("handle_webhook_notification is not yet implemented for Google Gmail enterprise")

    async def reindex_records(self, records: List[Record]) -> None:
        """Reindex records for Google Gmail enterprise."""
        raise NotImplementedError("reindex_records is not yet implemented for Google Gmail enterprise")

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> FilterOptionsResponse:
        """Google Gmail enterprise connector does not support dynamic filter options."""
        raise NotImplementedError("Google Gmail enterprise connector does not support dynamic filter options")

    async def cleanup(self) -> None:
        """Cleanup resources when shutting down the connector."""
        try:
            self.logger.info("Cleaning up Google Gmail enterprise connector resources")

            # Clear data source references
            if hasattr(self, 'gmail_data_source') and self.gmail_data_source:
                self.gmail_data_source = None

            if hasattr(self, 'admin_data_source') and self.admin_data_source:
                self.admin_data_source = None

            # Clear client references
            if hasattr(self, 'gmail_client') and self.gmail_client:
                self.gmail_client = None

            if hasattr(self, 'admin_client') and self.admin_client:
                self.admin_client = None

            # Clear config
            self.config = None

            self.logger.info("Google Gmail enterprise connector cleanup completed")

        except Exception as e:
            self.logger.error(f"❌ Error during cleanup: {e}")

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str
    ) -> BaseConnector:
        """Create a new instance of the Google Gmail enterprise connector."""
        data_entities_processor = DataSourceEntitiesProcessor(
            logger,
            data_store_provider,
            config_service
        )
        await data_entities_processor.initialize()

        return GoogleGmailTeamConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id
        )
