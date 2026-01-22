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
from app.connectors.core.registry.filters import FilterOptionsResponse
from app.connectors.sources.google.common.apps import GmailApp
from app.models.entities import (
    AppUser,
    Record,
    RecordGroup,
    RecordGroupType,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.google.google import GoogleClient
from app.sources.external.google.gmail.gmail import GoogleGmailDataSource
from app.utils.oauth_config import fetch_oauth_config_by_id


@ConnectorBuilder("Gmail")\
    .in_group("Google Workspace")\
    .with_description("Sync emails and messages from Gmail")\
    .with_categories(["Email"])\
    .with_scopes([ConnectorScope.PERSONAL.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Gmail",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            redirect_uri="connectors/oauth/callback/Gmail",
            scopes=OAuthScopeConfig(
                personal_sync=[
                    "https://www.googleapis.com/auth/gmail.readonly",
                    "https://www.googleapis.com/auth/gmail.metadata",
                ],
                team_sync=[],
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
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(True)
    )\
    .build_decorator()
class GoogleGmailIndividualConnector(BaseConnector):
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
        self.connector_id = connector_id

        # Batch processing configuration
        self.batch_size = 100

        # Gmail client and data source (initialized in init())
        self.gmail_client: Optional[GoogleClient] = None
        self.gmail_data_source: Optional[GoogleGmailDataSource] = None
        self.config: Optional[Dict] = None

    async def init(self) -> bool:
        """Initialize the Google Gmail connector with credentials and services."""
        try:
            # Load connector config
            config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config"
            )
            if not config:
                self.logger.error("Google Gmail config not found")
                return False

            self.config = {"credentials": config}

            # Extract auth configuration
            auth_config = config.get("auth")
            oauth_config_id = auth_config.get("oauthConfigId")

            if not oauth_config_id:
                self.logger.error("Gmail oauthConfigId not found in auth configuration.")
                return False

            # Fetch OAuth config
            oauth_config = await fetch_oauth_config_by_id(
                oauth_config_id=oauth_config_id,
                connector_type=Connectors.GOOGLE_MAIL.value,
                config_service=self.config_service,
                logger=self.logger
            )

            if not oauth_config:
                self.logger.error(f"OAuth config {oauth_config_id} not found for Gmail connector.")
                return False

            oauth_config_data = oauth_config.get("config", {})

            client_id = oauth_config_data.get("clientId")
            client_secret = oauth_config_data.get("clientSecret")

            if not all((client_id, client_secret)):
                self.logger.error(
                    "Incomplete Google Gmail config. Ensure clientId and clientSecret are configured."
                )
                raise ValueError(
                    "Incomplete Google Gmail credentials. Ensure clientId and clientSecret are configured."
                )

            # Extract credentials (tokens)
            credentials_data = config.get("credentials", {})
            access_token = credentials_data.get("access_token")
            refresh_token = credentials_data.get("refresh_token")

            if not access_token and not refresh_token:
                self.logger.warning(
                    "No access token or refresh token found. Connector may need OAuth flow completion."
                )

            # Initialize Google Client using build_from_services
            # This will handle token management and credential refresh automatically
            try:
                self.gmail_client = await GoogleClient.build_from_services(
                    service_name="gmail",
                    logger=self.logger,
                    config_service=self.config_service,
                    is_individual=True,  # This is an individual connector
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

            self.logger.info("✅ Google Gmail connector initialized successfully")
            return True

        except Exception as ex:
            self.logger.error(f"❌ Error initializing Google Gmail connector: {ex}", exc_info=True)
            raise

    async def test_connection_and_access(self) -> bool:
        """Test connection and access to Google Gmail."""
        try:
            self.logger.info("Testing connection and access to Google Gmail")
            if not self.gmail_data_source:
                self.logger.error("Gmail data source not initialized. Call init() first.")
                return False

            if not self.gmail_client:
                self.logger.error("Gmail client not initialized. Call init() first.")
                return False

            # Try to make a simple API call to test connection
            # For now, just check if client is initialized
            if self.gmail_client.get_client() is None:
                self.logger.warning("Google Gmail API client not initialized")
                return False

            return True
        except Exception as e:
            self.logger.error(f"❌ Error testing connection and access to Google Gmail: {e}")
            return False

    def get_signed_url(self, record: Record) -> Optional[str]:
        """Get a signed URL for a specific record."""
        raise NotImplementedError("get_signed_url is not yet implemented for Google Gmail")

    async def stream_record(self, record: Record, user_id: Optional[str] = None, convertTo: Optional[str] = None) -> StreamingResponse:
        """Stream a record from Google Gmail."""
        raise NotImplementedError("stream_record is not yet implemented for Google Gmail")

    async def _create_app_user(self, user_profile: Dict) -> None:
        """Create app user from Gmail profile."""
        try:
            email_address = user_profile.get("emailAddress")
            if not email_address:
                self.logger.error("Email address not found in Gmail profile")
                raise ValueError("Email address not found in Gmail profile")

            user = AppUser(
                email=email_address,
                full_name=email_address,  # Gmail profile doesn't provide display name
                source_user_id=email_address,
                app_name=Connectors.GOOGLE_MAIL.value,
                connector_id=self.connector_id
            )
            await self.data_entities_processor.on_new_app_users([user])
        except Exception as e:
            self.logger.error(f"❌ Error creating app user: {e}", exc_info=True)
            raise

    async def _create_personal_record_group(self, user_email: str) -> None:
        """Create personal record groups (INBOX, SENT, OTHERS) for the user."""
        try:
            if not user_email:
                self.logger.error("User email is required to create record groups")
                raise ValueError("User email is required to create record groups")

            self.logger.info(f"Creating record groups (INBOX, SENT, OTHERS) for user: {user_email}")
            total_record_groups_processed = 0

            # Create record groups for INBOX, SENT, and OTHERS
            for label_name in ["INBOX", "SENT", "OTHERS"]:
                try:
                    # Create record group name: "Gmail - {email} - {label_name}"
                    record_group_name = f"Gmail - {user_email} - {label_name}"

                    # Create external_group_id: "{email}:{label_name}"
                    external_group_id = f"{user_email}:{label_name}"

                    # Create record group
                    record_group = RecordGroup(
                        name=record_group_name,
                        org_id=self.data_entities_processor.org_id,
                        external_group_id=external_group_id,
                        description=f"Gmail label: {label_name}",
                        connector_name=self.connector_name,
                        connector_id=self.connector_id,
                        group_type=RecordGroupType.MAILBOX,
                    )

                    # Create owner permission from user to record group
                    owner_permission = Permission(
                        email=user_email,
                        type=PermissionType.OWNER,
                        entity_type=EntityType.USER
                    )

                    # Submit to processor
                    await self.data_entities_processor.on_new_record_groups(
                        [(record_group, [owner_permission])]
                    )

                    total_record_groups_processed += 1
                    self.logger.debug(
                        f"Created record group '{record_group_name}' for user {user_email}"
                    )

                except Exception as e:
                    self.logger.error(
                        f"Error creating record group '{label_name}' "
                        f"for user {user_email}: {e}",
                        exc_info=True
                    )
                    continue

            self.logger.info(
                f"✅ Successfully created {total_record_groups_processed} record groups "
                f"for user {user_email}"
            )

        except Exception as e:
            self.logger.error(f"❌ Error creating personal record groups: {e}", exc_info=True)
            raise

    async def run_sync(self) -> None:
        """Run sync for Google Gmail."""
        try:
            self.logger.info("Starting sync for Google Gmail Individual")

            # Get user profile
            user_profile = await self.gmail_data_source.users_get_profile(userId="me")
            await self._create_app_user(user_profile)

            # Extract email from profile
            user_email = user_profile.get("emailAddress")
            if not user_email:
                self.logger.error("Email address not found in user profile")
                raise ValueError("Email address not found in user profile")

            # Create personal record groups
            await self._create_personal_record_group(user_email)

            # Sync user's mailbox
            await self._sync_user_mailbox()

            self.logger.info("Sync completed for Google Gmail Individual")
        except Exception as e:
            self.logger.error(f"❌ Error during sync: {e}", exc_info=True)
            raise

    async def run_incremental_sync(self) -> None:
        """Run incremental sync for Google Gmail."""
        self.logger.info("Starting incremental sync for Google Gmail Individual")
        await self._run_sync()

    def handle_webhook_notification(self, notification: Dict) -> None:
        """Handle webhook notifications from Google Gmail."""
        raise NotImplementedError("handle_webhook_notification is not yet implemented for Google Gmail")

    async def cleanup(self) -> None:
        """Cleanup resources when shutting down the connector."""
        try:
            self.logger.info("Cleaning up Google Gmail connector resources")

            # Clear client and data source references
            if hasattr(self, 'gmail_data_source') and self.gmail_data_source:
                self.gmail_data_source = None

            if hasattr(self, 'gmail_client') and self.gmail_client:
                self.gmail_client = None

            # Clear config
            self.config = None

            self.logger.info("Google Gmail connector cleanup completed")

        except Exception as e:
            self.logger.error(f"❌ Error during cleanup: {e}")

    async def reindex_records(self, record_results: List[Record]) -> None:
        """Reindex records for Google Gmail."""
        raise NotImplementedError("reindex_records is not yet implemented for Google Gmail")

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> FilterOptionsResponse:
        """Google Gmail connector does not support dynamic filter options."""
        raise NotImplementedError("Google Gmail connector does not support dynamic filter options")

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str
    ) -> BaseConnector:
        """Create a new instance of the Google Gmail connector."""
        data_entities_processor = DataSourceEntitiesProcessor(
            logger,
            data_store_provider,
            config_service
        )
        await data_entities_processor.initialize()

        return GoogleGmailIndividualConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id
        )
