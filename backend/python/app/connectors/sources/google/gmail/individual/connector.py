import asyncio
import functools
import io
import re
import tempfile
import uuid
from logging import Logger
from pathlib import Path
from typing import TYPE_CHECKING, AsyncGenerator, Dict, List, Optional, Tuple

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    PermissionModel,
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
    RecordTypes,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.constants import IconPaths
from app.connectors.core.base.connector.connector_service import (
    BaseConnector,
    ConnectorInitError,
    oauth_app_not_linked_error,
)
from app.connectors.core.base.error.stream_errors import (
    connector_not_ready,
    map_source_status,
    not_downloadable,
    not_found_at_source,
    to_stream_error,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
    generate_record_sync_point_key,
)
from app.connectors.core.registry.auth_builder import AuthType, OAuthScopeConfig
from app.connectors.core.registry.connector_builder import (
    AuthBuilder,
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
    SyncStrategy,
)
from app.connectors.core.constants import CONNECTOR_EMAIL_IDENTITY_INFO
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
from app.connectors.core.registry.filters import (
    DatetimeOperator,
    FilterCategory,
    FilterCollection,
    FilterField,
    FilterOptionsResponse,
    FilterType,
    IndexingFilterKey,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.sources.google.common.apps import GmailIndividualApp
from app.connectors.sources.google.common.connector_google_exceptions import (
    GoogleAuthError,
    GoogleMailError,
    is_missing_scope_error,
)
from app.connectors.sources.google.common.datasource_refresh import (
    refresh_google_datasource_credentials,
)
from app.connectors.sources.google.common.gmail_call_policy import GmailCallPolicy
from app.connectors.sources.google.common.gmail_endpoint import gmail_api_base_url
from app.connectors.sources.google.common.gmail_errors import classify
from app.connectors.sources.google.common.gmail_mailbox_sync import (
    GmailMailboxApi,
    GmailMailboxSync,
    MailboxWindow,
)
from app.connectors.sources.google.common.gmail_message_parts import decode_body_data, find_message_part
from app.connectors.sources.google.common.gmail_notices import GmailNotices
from app.connectors.sources.google.common.gmail_sync_health import GmailSyncHealth
from app.connectors.sources.google.common.gmail_received_date_query import (
    build_gmail_received_date_threads_query,
)
from app.connectors.sources.google.gmail.talon_utils import quotations
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.models.entities import (
    AppUser,
    FileRecord,
    MailRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    USER_EMAIL_PLACEHOLDER,
)
from app.models.permission import Permission
from app.sources.client.google.google import GOOGLE_HTTP_NUM_RETRIES, GoogleClient
from app.sources.external.google.drive.drive import GoogleDriveDataSource
from app.sources.external.google.gmail.gmail import GoogleGmailDataSource
from app.utils.filename_utils import temp_path_for
from app.utils.pdf_stream_conversion import libreoffice_to_pdf
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.connectors.core.thread_pool import ThreadPoolLease

# Maximum concurrent borrows from the shared connector thread pool.
_GMAIL_INDIVIDUAL_MAX_CONCURRENCY = 4

# Bytes fetched per MediaIoBaseDownload.next_chunk() call. The library default is
# 100 MB, which buffers a whole slice in memory before any of it reaches the
# client and keeps one executor thread busy for that entire transfer.
_GMAIL_DOWNLOAD_CHUNK_SIZE = 4 * 1024 * 1024


@ConnectorBuilder("Gmail")\
    .in_group("Google Workspace")\
    .with_description("Sync emails and messages from Gmail")\
    .with_categories(["Email"])\
    .with_scopes([ConnectorScope.PERSONAL.value])\
    .with_permission_model(PermissionModel.APP_LEVEL)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Gmail",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            redirect_uri="connectors/oauth/callback/Gmail",
            scopes=OAuthScopeConfig(
                personal_sync=[
                    "https://www.googleapis.com/auth/gmail.readonly",
                ],
                team_sync=[],
                agent=[]
            ),
            fields=[
                CommonFields.client_id("Google Cloud Console"),
                CommonFields.client_secret("Google Cloud Console")
            ],
            icon_path=IconPaths.connector_icon(Connectors.GOOGLE_MAIL.value),
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
    .with_info(CONNECTOR_EMAIL_IDENTITY_INFO)\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.GOOGLE_MAIL.value))
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
        .add_filter_field(FilterField(
            name=SyncFilterKey.RECEIVED_DATE.value,
            display_name="Received Date",
            description="Filter emails by received date. Defaults to last 90 days.",
            filter_type=FilterType.DATETIME,
            category=FilterCategory.SYNC,
            default_operator=DatetimeOperator.LAST_90_DAYS.value,
            default_value=None
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
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
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
        connector_id: str,
        scope: str,
        created_by: str
    ) -> None:
        super().__init__(
            GmailIndividualApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by
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

        # Filter collections
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

        # Gmail client and data source (initialized in init())
        self.gmail_client: Optional[GoogleClient] = None
        self.gmail_data_source: Optional[GoogleGmailDataSource] = None
        self.config: Optional[Dict] = None
        self._datasource_refresh_lock = asyncio.Lock()

        # What the current run could not do; decides its outcome and what a full sync may remove.
        self._sync_health = GmailSyncHealth()
        self._call_policy = GmailCallPolicy(self.logger)

        # Acquired in init(), once the factory has injected the shared pool.
        self._gmail_executor: ThreadPoolLease | None = None

    async def init(self) -> bool:
        """Initialize the Google Gmail connector with credentials and services."""
        try:
            self._gmail_executor = self._thread_lease(_GMAIL_INDIVIDUAL_MAX_CONCURRENCY)
            if gmail_api_base_url():
                self.logger.warning(
                    "Gmail calls for this connector go to %s (GMAIL_GOOGLE_API_BASE_URL), not to Google: "
                    "only a test deployment should set it.", gmail_api_base_url()
                )

            # Load connector config
            config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config"
            )
            if not config:
                self.logger.error("Google Gmail config not found")
                return False

            self.config = {"credentials": config}

            # Extract auth configuration
            auth_config = config.get("auth") or {}
            oauth_config_id = auth_config.get("oauthConfigId")

            if not oauth_config_id:
                self.logger.error(
                    "Gmail has no oauthConfigId in its auth config; it reads its "
                    "client ID and secret only from a linked OAuth app."
                )
                raise oauth_app_not_linked_error()

            oauth_config = await self._fetch_oauth_config_by_id(
                oauth_config_id=oauth_config_id,
                connector_type=Connectors.GOOGLE_MAIL.value,
                auth_config=auth_config,
            )

            if not oauth_config:
                self.logger.error(f"Linked OAuth app not found for Gmail connector {self.connector_id}.")
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
                    connector_instance_id=self.connector_id,
                    api_base_url=gmail_api_base_url(),
                )

                # Create Google Gmail Data Source from the client
                self.gmail_data_source = GoogleGmailDataSource(
                    self.gmail_client.get_client(),
                    executor=self._gmail_executor,
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

        except ConnectorInitError:
            raise
        except Exception as ex:
            self.logger.error(f"❌ Error initializing Google Gmail connector: {ex}", exc_info=True)
            raise

    async def _get_fresh_datasource(self) -> None:
        """
        Ensure gmail_data_source has ALWAYS-FRESH OAuth credentials.

        Creates a new Credentials object when credentials change.
        After calling this, use self.gmail_data_source directly.

        The datasource wraps a Google client by reference, so replacing
        the client's credentials automatically updates the datasource.
        """
        if not self.gmail_client or not self.gmail_data_source:
            raise GoogleMailError("Gmail client or Gmail data source not initialized. Call init() first.")


        async with self._datasource_refresh_lock:
            await refresh_google_datasource_credentials(
                google_client=self.gmail_client,
                data_source=self.gmail_data_source,
                config_service=self.config_service,
                connector_id=self.connector_id,
                logger=self.logger,
                service_name="Gmail"
            )

    async def _get_existing_record(self, external_record_id: str) -> Optional[Record]:
        """Get existing record from data store."""
        try:
            return await self.data_entities_processor.get_record_by_external_id(
                self.connector_id, external_record_id
            )
        except Exception as e:
            self.logger.error(f"Error getting existing record {external_record_id}: {e}")
            return None

    @staticmethod
    def _mailbox_external_group_id(user_email: str, label_ids: Optional[List[str]]) -> str:
        """Mailbox record-group external id from Gmail labelIds (same rule as MailRecord)."""
        labels = label_ids or []
        if "SENT" in labels:
            return f"{user_email}:SENT"
        if "INBOX" in labels:
            return f"{user_email}:INBOX"
        return f"{user_email}:OTHERS"

    def _parse_gmail_headers(self, headers: List[Dict]) -> Dict[str, str]:
        """
        Parse Gmail message headers into a dictionary.

        Args:
            headers: List of header dictionaries from Gmail API

        Returns:
            Dictionary mapping header names to values
        """
        parsed_headers = {}

        for header in headers:
            name = header.get('name', '').lower()
            value = header.get('value', '')

            if name in ['subject', 'from', 'to', 'cc', 'bcc', 'message-id', 'date']:
                parsed_headers[name] = value

        return parsed_headers

    def _parse_email_list(self, email_string: str) -> List[str]:
        """
        Parse comma-separated email string into list of emails.

        Args:
            email_string: Comma-separated email addresses

        Returns:
            List of email addresses
        """
        if not email_string:
            return []

        # Split by comma and clean up
        emails = [email.strip() for email in email_string.split(',')]
        # Filter out empty strings
        return [email for email in emails if email]

    def _extract_email_from_header(self, email_header: str) -> str:
        """
        Extract email address from email header field.
        Handles formats like "Name <email@example.com>" or just "email@example.com".

        Args:
            email_header: Email header value (may contain name and email)

        Returns:
            Extracted email address
        """
        if not email_header:
            return ""

        email_header = email_header.strip()

        # Check if email is in format "Name <email@example.com>"
        if "<" in email_header and ">" in email_header:
            start = email_header.find("<") + 1
            end = email_header.find(">")
            if start > 0 and end > start:
                return email_header[start:end].strip()

        # Otherwise, return the whole string (assuming it's just an email)
        return email_header

    async def _process_gmail_message(
        self,
        user_email: str,
        message: Dict,
        thread_id: str,
        previous_message_id: Optional[str],
    ) -> Optional[RecordUpdate]:
        """
        Process a single Gmail message and create a MailRecord.

        Args:
            user_email: Email of the user who owns the message
            message: Message data from Gmail API
            thread_id: Thread ID this message belongs to
            previous_message_id: ID of previous message in thread (for sibling relation)

        Returns:
            RecordUpdate object or None
        """
        try:
            # Extract message metadata
            message_id = message.get('id')
            if not message_id:
                return None

            # Extract labelIds from message
            label_ids = message.get('labelIds', [])
            internal_date = message.get('internalDate')  # Epoch milliseconds as string

            # Determine external_record_group_id based on labelIds (SENT or INBOX)
            external_record_group_id = self._mailbox_external_group_id(user_email, label_ids)

            # Parse headers
            payload = message.get('payload', {})
            headers = payload.get('headers', [])
            parsed_headers = self._parse_gmail_headers(headers)

            # Extract header fields
            subject = parsed_headers.get('subject', '(No Subject)')
            from_email = parsed_headers.get('from', '')
            to_emails_str = parsed_headers.get('to', '')
            cc_emails_str = parsed_headers.get('cc', '')
            bcc_emails_str = parsed_headers.get('bcc', '')
            internet_message_id = parsed_headers.get('message-id', '')

            # Parse email lists
            to_emails = self._parse_email_list(to_emails_str)
            cc_emails = self._parse_email_list(cc_emails_str)
            bcc_emails = self._parse_email_list(bcc_emails_str)

            # Convert internal_date to milliseconds
            source_created_at = None
            if internal_date:
                try:
                    source_created_at = int(internal_date)
                except (ValueError, TypeError):
                    source_created_at = get_epoch_timestamp_in_ms()
            else:
                source_created_at = get_epoch_timestamp_in_ms()

            # Check for existing record
            existing_record = await self._get_existing_record(message_id)
            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False

            if not is_new:
                # Check if thread_id/external_record_group_id changed (metadata change)
                current_external_group_id = external_record_group_id
                existing_external_group_id = existing_record.external_record_group_id if hasattr(existing_record, 'external_record_group_id') else None
                if existing_external_group_id and current_external_group_id != existing_external_group_id:
                    metadata_changed = True
                    is_updated = True
                    self.logger.info(f"Gmail message {message_id} external_record_group_id changed: {existing_external_group_id} -> {current_external_group_id}")

            record_id = existing_record.id if existing_record else str(uuid.uuid4())

            # Create MailRecord
            mail_record = MailRecord(
                id=record_id,
                org_id=self.data_entities_processor.org_id,
                record_name=subject[:255] if subject else "(No Subject)",  # Truncate if too long
                record_type=RecordType.MAIL,
                record_group_type=RecordGroupType.MAILBOX,
                external_record_id=message_id,
                external_record_group_id=external_record_group_id,
                thread_id=thread_id,
                label_ids=label_ids,
                version=0 if is_new else existing_record.version + 1,
                origin=OriginTypes.CONNECTOR,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                created_at=get_epoch_timestamp_in_ms(),
                updated_at=get_epoch_timestamp_in_ms(),
                source_created_at=source_created_at,
                source_updated_at=source_created_at,
                mime_type=MimeTypes.GMAIL.value,
                weburl=f"https://mail.google.com/mail?authuser={USER_EMAIL_PLACEHOLDER}#all/{message_id}",
                inherit_permissions=True,
                preview_renderable=False,
                subject=subject,
                from_email=from_email,
                to_emails=to_emails,
                cc_emails=cc_emails,
                bcc_emails=bcc_emails,
                internet_message_id=internet_message_id,
            )

            # App-level: the creator passes the gate. A message inherits from its
            # label, and the label inherits from the app.
            permissions: list[Permission] = []

            self.logger.debug(
                f"Processed message {message_id} in thread {thread_id}: "
                f"{subject[:50]}..."
            )

            return RecordUpdate(
                record=mail_record,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=False,
                metadata_changed=metadata_changed,
                content_changed=content_changed,  # Gmail messages are immutable
                permissions_changed=bool(permissions),
                new_permissions=permissions,
                external_record_id=message_id,
            )

        except Exception as e:
            self.logger.error(
                f"Error processing Gmail message {message.get('id', 'unknown')}: {e}",
                exc_info=True
            )
            return None

    async def _process_gmail_message_generator(
        self,
        messages: List[Dict],
        user_email: str,
        thread_id: str
    ) -> AsyncGenerator[Optional[RecordUpdate], None]:
        """
        Process Gmail messages and yield RecordUpdate objects.
        Generator for non-blocking processing of large datasets.

        Args:
            messages: List of Gmail message dictionaries
            user_email: Email of the user who owns the messages
            thread_id: Thread ID these messages belong to
        """
        for message in messages:
            try:
                message_update = await self._process_gmail_message(
                    user_email,
                    message,
                    thread_id,
                    None  # previous_message_id is handled in caller for sibling relations
                )

                if message_update:
                    if message_update.record and not self.indexing_filters.is_enabled(IndexingFilterKey.MAILS, default=True):
                        message_update.record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

                    yield message_update

                # Allow other tasks to run
                await asyncio.sleep(0)

            except Exception as e:
                self.logger.error(f"Error processing message in generator: {e}", exc_info=True)
                continue

    def _extract_attachment_infos(self, message: Dict) -> List[Dict]:
        """Extract attachment info from Gmail message payload.

        Args:
            message: Message data from Gmail API

        Returns:
            List of attachment info dictionaries with stable IDs
        """
        attachment_infos = []
        payload = message.get('payload', {})
        parts = payload.get('parts', [])
        message_id = message.get('id')
        logger = self.logger  # Capture logger for nested functions

        def extract_drive_file_ids_from_content(body_data: str) -> List[str]:
            """Extract Drive file IDs from base64-encoded body content.

            Matches the pattern used in gmail_user_service.py get_file_ids method.
            """
            if not body_data:
                return []
            try:
                decoded_data = decode_body_data(body_data).decode('UTF-8')

                # Match Drive file URLs: https://drive.google.com/file/d/{file_id}/view?usp=drive_web
                # This matches the exact pattern used in gmail_user_service.py
                file_ids = re.findall(
                    r'https://drive\.google\.com/file/d/([^/]+)/view\?usp=drive_web',
                    decoded_data
                )

                return file_ids
            except Exception as e:
                logger.warning(f"Failed to decode content for Drive file extraction: {str(e)}")
                return []

        def process_part_for_drive_files(part: Dict) -> List[str]:
            """Recursively process parts to extract Drive file IDs from body content.

            Matches the pattern used in gmail_user_service.py get_file_ids method.
            """
            if not isinstance(part, dict):
                return []

            file_ids = []

            # Check for body data
            body = part.get("body", {})
            if isinstance(body, dict) and body.get("data"):
                mime_type = part.get("mimeType", "")
                if "text/html" in mime_type or "text/plain" in mime_type:
                    file_ids.extend(extract_drive_file_ids_from_content(body["data"]))

            # Recursively process nested parts
            parts = part.get("parts", [])
            if isinstance(parts, list):
                for nested_part in parts:
                    file_ids.extend(process_part_for_drive_files(nested_part))

            return file_ids

        def extract_attachments(parts_list) -> List[Dict]:
            """Recursively extract attachments from message parts."""
            attachments = []
            seen_drive_file_ids = set()  # Track to avoid duplicates

            for part in parts_list:
                if not isinstance(part, dict):
                    continue
                body = part.get('body', {})
                part_id = part.get('partId', 'unknown')
                mime_type = part.get('mimeType', '')

                # Check if this part is a regular attachment (has filename)
                if part.get('filename'):
                    attachment_id = body.get('attachmentId')
                    drive_file_id = body.get('driveFileId')  # For attachments >25MB

                    # Handle Drive attachments (>25MB) with driveFileId in body
                    if drive_file_id:
                        seen_drive_file_ids.add(drive_file_id)
                        # For Drive attachments, use driveFileId as external_record_id
                        attachments.append({
                            'attachmentId': None,  # Not available for Drive files
                            'driveFileId': drive_file_id,  # Use Drive file ID
                            'stableAttachmentId': drive_file_id,  # Use Drive ID as stable ID
                            'partId': part_id,
                            'filename': part.get('filename'),
                            'mimeType': mime_type,
                            'size': body.get('size', 0),
                            'isDriveFile': True
                        })
                    # Handle regular attachments (≤25MB)
                    elif attachment_id:
                        # Construct stable ID using message_id + partId
                        stable_attachment_id = f"{message_id}~{part_id}"

                        attachments.append({
                            'attachmentId': attachment_id,  # Volatile - for downloading
                            'driveFileId': None,  # Not a Drive file
                            'stableAttachmentId': stable_attachment_id,  # Stable - for record ID
                            'partId': part_id,
                            'filename': part.get('filename'),
                            'mimeType': mime_type,
                            'size': body.get('size', 0),
                            'isDriveFile': False
                        })

                # Recursively check nested parts
                if part.get('parts'):
                    attachments.extend(extract_attachments(part.get('parts')))

            return attachments

        # First, extract regular attachments (with filename) and Drive attachments with driveFileId in body
        attachment_infos = extract_attachments(parts)
        seen_drive_file_ids = {att.get('driveFileId') for att in attachment_infos if att.get('driveFileId')}

        # Then, extract Drive file IDs from message body content (matching gmail_user_service.py pattern)
        # Start processing from the payload (matching gmail_user_service.py get_file_ids)
        if isinstance(payload, dict):

            all_drive_file_ids = process_part_for_drive_files(payload)
            # Remove duplicates while preserving order (matching gmail_user_service.py)
            unique_drive_file_ids = list(dict.fromkeys(all_drive_file_ids))

            for drive_file_id in unique_drive_file_ids:
                if drive_file_id and drive_file_id not in seen_drive_file_ids:
                    seen_drive_file_ids.add(drive_file_id)
                    attachment_infos.append({
                        'attachmentId': None,  # Not available for Drive files
                        'driveFileId': drive_file_id,  # Use Drive file ID
                        'stableAttachmentId': drive_file_id,  # Use Drive ID as stable ID
                        'partId': 'unknown',  # Not associated with a specific part
                        'filename': None,  # Filename not available from link, will be fetched from Drive API
                        'mimeType': 'application/vnd.google-apps.file',  # Default for Drive files
                        'size': 0,  # Size not available from link, will be fetched from Drive API
                        'isDriveFile': True
                    })

        return attachment_infos

    async def _process_gmail_attachment(
        self,
        user_email: str,
        message_id: str,
        attachment_info: Dict,
        parent_mail_permissions: List[Permission],
        external_record_group_id: str,
    ) -> Optional[RecordUpdate]:
        """
        Process a single Gmail attachment and create a FileRecord.

        Args:
            user_email: Email of the user who owns the message
            message_id: ID of the parent message
            attachment_info: Attachment metadata dict with attachmentId, driveFileId, filename, mimeType, size
            parent_mail_permissions: Permissions from parent mail (attachments inherit these)
            external_record_group_id: Mailbox group key from parent message labelIds (matches MailRecord)

        Returns:
            RecordUpdate object or None
        """
        try:

            attachment_id = attachment_info.get('attachmentId')
            drive_file_id = attachment_info.get('driveFileId')
            filename = attachment_info.get('filename') or 'unnamed_attachment'  # Handle None case
            mime_type = attachment_info.get('mimeType', 'application/octet-stream')
            size = attachment_info.get('size', 0)
            stable_attachment_id = attachment_info.get('stableAttachmentId')
            is_drive_file = attachment_info.get('isDriveFile', False)

            # Must have either attachmentId (regular) or driveFileId (Drive)
            if not stable_attachment_id:
                return None

            if not is_drive_file and not attachment_id:
                return None

            # For Drive files, always fetch metadata from Drive API
            if is_drive_file and drive_file_id:
                try:
                    # Create Drive client for the user (individual connector)
                    user_drive_client = await GoogleClient.build_from_services(
                        service_name="drive",
                        logger=self.logger,
                        config_service=self.config_service,
                        is_individual=True,  # Individual connector
                        version="v3",
                        connector_instance_id=self.connector_id,
                        api_base_url=gmail_api_base_url(),
                    )


                    drive_service = user_drive_client.get_client()

                    # Fetch file metadata. get_media()/execute() is a synchronous HTTP
                    # call, so run it off the event loop to avoid blocking other work.
                    metadata_request = drive_service.files().get(
                        fileId=drive_file_id,
                        fields="id,name,mimeType,size"
                    )
                    drive_data_source = GoogleDriveDataSource(
                        drive_service,
                        executor=self._gmail_executor,
                    )
                    file_metadata = await drive_data_source.execute(
                        metadata_request.execute
                    )

                    if file_metadata:
                        filename = file_metadata.get("name", "unnamed_attachment")
                        mime_type = file_metadata.get("mimeType", "application/octet-stream")
                        size = int(file_metadata.get("size", 0))
                        self.logger.info(
                            f"✅ Fetched Drive file metadata for {drive_file_id}: {filename} ({size} bytes, {mime_type})"
                        )
                except Exception as e:
                    if is_missing_scope_error(e):
                        # A record here could never be downloaded, so it would only
                        # sit in the index as a failed file.
                        self.logger.info(
                            f"Skipping the Google Drive file {drive_file_id} linked from message "
                            f"{message_id}: this Gmail connection is allowed to read mail but "
                            "not Google Drive, so the file can't be opened. The email itself "
                            "is still indexed."
                        )
                        return None
                    self.logger.warning(
                        f"⚠️ Failed to fetch Drive file metadata for {drive_file_id}: {str(e)}"
                    )
                    # Continue with existing values from attachment_info

            # Check for existing record
            existing_record = await self._get_existing_record(stable_attachment_id)
            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False  # Gmail attachments are immutable

            # Extract file extension from filename
            extension = None
            if filename and '.' in filename:
                extension = filename.rsplit('.', 1)[-1].lower()

            record_id = existing_record.id if existing_record else str(uuid.uuid4())

            # Create FileRecord
            # For Drive files, use driveFileId as external_record_id
            # For regular attachments, use stable_attachment_id (message_id_partId)
            file_record = FileRecord(
                id=record_id,
                org_id=self.data_entities_processor.org_id,
                record_name=filename,
                record_type=RecordType.FILE,
                record_group_type=RecordGroupType.MAILBOX,
                external_record_id=stable_attachment_id,  # driveFileId for Drive files, stable ID for regular
                external_record_group_id=external_record_group_id,
                parent_external_record_id=message_id,
                parent_record_type=RecordType.MAIL,
                version=0 if is_new else existing_record.version + 1,
                origin=OriginTypes.CONNECTOR,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                created_at=get_epoch_timestamp_in_ms(),
                updated_at=get_epoch_timestamp_in_ms(),
                source_created_at=get_epoch_timestamp_in_ms(),
                source_updated_at=get_epoch_timestamp_in_ms(),
                mime_type=mime_type,
                weburl=f"https://mail.google.com/mail?authuser={USER_EMAIL_PLACEHOLDER}#all/{message_id}",
                inherit_permissions=True,
                size_in_bytes=size,
                extension=extension,
                is_file=True,
                is_dependent_node=True,
            )

            # Check indexing filter for attachments
            if not self.indexing_filters.is_enabled(IndexingFilterKey.ATTACHMENTS, default=True):
                file_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

            del parent_mail_permissions
            attachment_permissions: list[Permission] = []

            attachment_identifier = drive_file_id if is_drive_file else attachment_id
            self.logger.debug(
                f"Processed attachment {attachment_identifier} ({'Drive' if is_drive_file else 'Gmail'}): "
                f"{filename} ({size} bytes)"
            )

            return RecordUpdate(
                record=file_record,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=False,
                metadata_changed=metadata_changed,
                content_changed=content_changed,  # Gmail attachments are immutable
                permissions_changed=bool(attachment_permissions),
                new_permissions=attachment_permissions,
                external_record_id=stable_attachment_id,
            )

        except Exception as e:
            attachment_identifier = attachment_info.get('driveFileId') or attachment_info.get('attachmentId', 'unknown')
            self.logger.error(f"Error processing Gmail attachment {attachment_identifier}: {e}")
            return None

    async def _process_gmail_attachment_generator(
        self,
        user_email: str,
        message_id: str,
        attachment_infos: List[Dict],
        parent_mail_permissions: List[Permission],
        external_record_group_id: str,
    ) -> AsyncGenerator[Optional[RecordUpdate], None]:
        """
        Process Gmail attachments and yield RecordUpdate objects.
        Generator for non-blocking processing of large datasets.

        Args:
            user_email: Email of the user who owns the message
            message_id: ID of the parent message
            attachment_infos: List of attachment metadata dictionaries
            parent_mail_permissions: Permissions from parent mail (attachments inherit these)
            external_record_group_id: Mailbox group key from parent message labelIds
        """
        for attach_info in attachment_infos:
            try:
                attach_update = await self._process_gmail_attachment(
                    user_email,
                    message_id,
                    attach_info,
                    parent_mail_permissions,
                    external_record_group_id,
                )

                if attach_update:
                    if attach_update.record and not self.indexing_filters.is_enabled(IndexingFilterKey.ATTACHMENTS, default=True):
                        attach_update.record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

                    yield attach_update

                # Allow other tasks to run
                await asyncio.sleep(0)

            except Exception as e:
                self.logger.error(f"Error processing attachment in generator: {e}", exc_info=True)
                continue

    async def _sync_user_mailbox(self, user_email: str) -> None:
        """Syncs the connected mailbox: a full read, or the changes since the last sync (`GmailMailboxSync`)."""
        if not self.gmail_data_source:
            raise GoogleMailError("Gmail data source not initialized. Call init() first.")

        async def datasource() -> GoogleGmailDataSource:
            await self._get_fresh_datasource()
            return self.gmail_data_source

        received = self.sync_filters.get(SyncFilterKey.RECEIVED_DATE)
        window = MailboxWindow(
            q=build_gmail_received_date_threads_query(received),
            start_ms=received.get_datetime_start() if received else None,
            end_ms=received.get_datetime_end() if received else None,
        )

        async def build_records(message: Dict, thread_id: str) -> list[tuple[Record, list[Permission]]]:
            return await self._records_for_message(user_email, message, thread_id)

        self.logger.info(f"Starting sync for user {user_email}")
        mailbox = GmailMailboxSync(
            mailbox=user_email,
            api=GmailMailboxApi(datasource, "me", self._call_policy),
            build_records=build_records,
            processor=self.data_entities_processor,
            connector_id=self.connector_id,
            sync_point=self.gmail_delta_sync_point,
            sync_point_key=generate_record_sync_point_key(RecordType.MAIL.value, "user", user_email),
            health=self._sync_health,
            logger=self.logger,
            window=window,
            batch_size=self.batch_size,
        )
        await mailbox.run()
        self.logger.info(f"Finished sync for user {user_email}: {mailbox.stats}")

    async def _records_for_message(
        self, user_email: str, message: Dict, thread_id: str
    ) -> list[tuple[Record, list[Permission]]]:
        """A message's record followed by its attachments' records, with the indexing filters applied."""
        out: list[tuple[Record, list[Permission]]] = []
        async for mail_update in self._process_gmail_message_generator([message], user_email, thread_id):
            if not mail_update or not mail_update.record:
                continue
            mail_record = mail_update.record
            out.append((mail_record, mail_update.new_permissions or []))
            out.extend([
                (attach_update.record, attach_update.new_permissions or [])
                async for attach_update in self._process_gmail_attachment_generator(
                    user_email,
                    mail_record.external_record_id,
                    self._extract_attachment_infos(message),
                    mail_update.new_permissions or [],
                    mail_record.external_record_group_id,
                )
                if attach_update and attach_update.record
            ])
        return out

    async def test_connection_and_access(self) -> bool:
        """Reads the connected mailbox's profile: fails when Google no longer accepts the connection. Never
        raises."""
        self.logger.info("Testing connection and access to Google Gmail")
        try:
            if not self.gmail_data_source or not self.gmail_client:
                self.logger.error("Gmail client not initialized. Call init() first.")
                return False
            policy = GmailCallPolicy(self.logger, max_wait_s=15)
            await self._get_fresh_datasource()
            await policy.run(lambda: self.gmail_data_source.users_get_profile(userId="me"), "mailbox check")
            return True
        except Exception as e:
            self.logger.error(f"❌ Gmail connection test failed ({classify(e).value}): {e}")
            return False

    def get_signed_url(self, record: Record) -> Optional[str]:
        """Get a signed URL for a specific record."""
        raise NotImplementedError("get_signed_url is not yet implemented for Google Gmail")

    def _extract_body_from_payload(self, payload: dict) -> str:
        """
        Recursively search for body content in a nested Gmail payload.
        """
        mime_type = payload.get("mimeType")
        body_data = payload.get("body", {}).get("data", "")

        # Base Case: Found the content
        # You can prioritize 'text/html' by checking for it first in the parts loop
        if mime_type in ["text/html", "text/plain"] and body_data:
            return body_data

        # Recursive Step: Look into parts
        if "parts" in payload:
            parts = payload.get("parts", [])

            # Strategy: Try to find HTML first for better formatting
            for part in parts:
                if part.get("mimeType") == "text/html":
                    content = self._extract_body_from_payload(part)
                    if content:
                        return content

            # Fallback: Find anything else (like text/plain)
            for part in parts:
                content = self._extract_body_from_payload(part)
                if content:
                    return content

        return ""

    async def _convert_to_pdf(self, file_path: str, temp_dir: str) -> str:
        """Convert ``file_path``, an absolute path inside a private temp dir, to PDF."""
        return await libreoffice_to_pdf(file_path, temp_dir)

    async def _stream_from_drive(
        self,
        drive_file_id: str,
        record: Record,
        file_name: str,
        mime_type: str,
        convertTo: Optional[str] = None
    ) -> StreamingResponse:
        """
        Stream a file from Google Drive (used for Drive attachments and as fallback).

        Args:
            drive_file_id: Google Drive file ID
            record: Record object
            file_name: Name of the file
            mime_type: MIME type of the file
            convertTo: Optional format to convert to (e.g., "application/pdf")

        Returns:
            StreamingResponse with file content
        """
        try:
            # Create Drive client for the user (individual connector)
            try:
                user_drive_client = await GoogleClient.build_from_services(
                    service_name="drive",
                    logger=self.logger,
                    config_service=self.config_service,
                    is_individual=True,  # Individual connector
                    version="v3",
                    connector_instance_id=self.connector_id,
                    api_base_url=gmail_api_base_url(),
                )
                drive_service = user_drive_client.get_client()
                self.logger.info("Using user OAuth credentials for Drive access")
            # A personal connection has no service account to fall back on.
            except GoogleAuthError as e:
                self.logger.error(f"Failed to create Drive client: {e}")
                raise HTTPException(
                    status_code=HttpStatusCode.CONFLICT.value,
                    detail="Couldn't connect to Google Drive to download this file. Reconnect the Gmail connector, then try again."
                ) from e
            except Exception as e:
                self.logger.error(f"Failed to load the Drive client's settings: {e}")
                raise HTTPException(
                    status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                    detail="Couldn't load the Gmail connector's settings to download this file. Try again in a moment; if it keeps failing, check the connector's configuration."
                ) from e

            drive_data_source = GoogleDriveDataSource(
                drive_service,
                executor=self._gmail_executor,
            )

            if convertTo == MimeTypes.PDF.value:
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_file_path = temp_path_for(temp_dir, file_name)

                    # Download from Drive to temp file
                    with open(temp_file_path, "wb") as f:
                        request = drive_service.files().get_media(
                            fileId=drive_file_id
                        )
                        downloader = MediaIoBaseDownload(f, request, chunksize=_GMAIL_DOWNLOAD_CHUNK_SIZE)

                        done = False
                        while not done:
                            # next_chunk() performs the HTTP range request synchronously, so
                            # calling it here would freeze the event loop for the whole
                            # round-trip and stall every other request in the process.
                            status, done = await drive_data_source.execute(
                                downloader.next_chunk
                            )
                            self.logger.info(
                                f"Download {int(status.progress() * 100)}%."
                            )

                    # Convert to PDF
                    pdf_path = await self._convert_to_pdf(
                        temp_file_path, temp_dir
                    )
                    return create_stream_record_response(
                        open(pdf_path, "rb"),
                        filename=f"{Path(file_name).stem}",
                        mime_type="application/pdf",
                        fallback_filename=f"record_{record.id}"
                    )

            # Use the same streaming logic as Drive downloads
            async def file_stream() -> AsyncGenerator[bytes, None]:
                buffer = io.BytesIO()
                chunk_count = 0
                total_bytes = 0
                try:
                    request = drive_service.files().get_media(
                        fileId=drive_file_id
                    )
                    downloader = MediaIoBaseDownload(buffer, request, chunksize=_GMAIL_DOWNLOAD_CHUNK_SIZE)
                    done = False

                    self.logger.info(f"Starting Drive file stream for {drive_file_id}")

                    while not done:
                        try:
                            # next_chunk() performs the HTTP range request synchronously, so
                            # calling it here would freeze the event loop for the whole
                            # round-trip and stall every other request in the process.
                            status, done = await drive_data_source.execute(
                                downloader.next_chunk
                            )
                            progress = int(status.progress() * 100)
                            self.logger.info(
                                f"Download {progress}%."
                            )

                            buffer.seek(0)
                            content = buffer.read()

                            if content:  # Only yield if we have data
                                chunk_count += 1
                                total_bytes += len(content)
                                self.logger.debug(
                                    f"Yielding chunk {chunk_count}, size: {len(content)} bytes, total: {total_bytes} bytes"
                                )
                                yield content

                            # Clear buffer for next chunk
                            buffer.seek(0)
                            buffer.truncate(0)

                            # Yield control back to event loop
                            await asyncio.sleep(0)

                        except HttpError as http_error:
                            self.logger.error(f"HTTP error during Drive download: {str(http_error)}")
                            raise map_source_status(
                                http_error.resp.status, connector=self.display_name
                            ) from http_error
                        except Exception as chunk_error:
                            self.logger.error(f"Error downloading chunk: {str(chunk_error)}")
                            raise to_stream_error(
                                chunk_error, connector=self.display_name
                            ) from chunk_error

                    self.logger.info(
                        f"Drive file stream completed: {chunk_count} chunks, {total_bytes} total bytes"
                    )

                except HTTPException:
                    raise
                except Exception as stream_error:
                    self.logger.error(f"Error in file stream: {str(stream_error)}", exc_info=True)
                    raise to_stream_error(
                        stream_error, connector=self.display_name
                    ) from stream_error
                finally:
                    self.logger.debug(f"Closing buffer for Drive file {drive_file_id}")
                    buffer.close()

            return create_stream_record_response(
                file_stream(),
                filename=file_name,
                mime_type=mime_type,
                fallback_filename=f"record_{record.id}"
            )

        except HTTPException:
            raise
        except Exception as drive_error:
            self.logger.error(f"Failed to stream Drive file {drive_file_id}: {str(drive_error)}")
            raise to_stream_error(drive_error, connector=self.display_name) from drive_error

    async def _stream_call(self, request: object, what: str) -> Dict:
        """One read for preview or indexing, retried through a brief rate limit or outage."""
        policy = GmailCallPolicy(self.logger, max_wait_s=60)
        return await policy.run(
            lambda: self.gmail_data_source.execute(
                functools.partial(request.execute, num_retries=GOOGLE_HTTP_NUM_RETRIES)
            ),
            what,
        )

    async def _stream_mail_record(
        self,
        gmail_service,
        message_id: str,
        record: Record
    ) -> StreamingResponse:
        try:
            # 1. Fetch message. execute() is a synchronous HTTP call, so run it off
            # the event loop to avoid blocking other work.
            request = (
                gmail_service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
            )
            message = await self._stream_call(request, "Gmail read")

            # 2. Extract payload (HTML)
            mail_content_base64 = self._extract_body_from_payload(message.get("payload", {}))
            raw_html = decode_body_data(mail_content_base64).decode("utf-8", errors="replace")

            latest_reply_html = ""

            if raw_html:
                try:
                    latest_reply_html = quotations.extract_from_html(raw_html)
                except Exception as extract_error:
                    self.logger.warning(
                        f"Failed to extract latest reply from HTML for message {message_id}: {extract_error}"
                    )
                    latest_reply_html = raw_html
                if not latest_reply_html:
                    latest_reply_html = raw_html


            async def message_stream() -> AsyncGenerator[bytes, None]:
                yield latest_reply_html.encode("utf-8")

            return create_stream_record_response(
                message_stream(),
                filename=f"{record.record_name}",
                mime_type="text/html",
                fallback_filename=f"record_{record.id}"
            )

        except HTTPException:
            raise
        except HttpError as http_error:
            self.logger.error(f"Failed to fetch mail content for {message_id}: {str(http_error)}")
            raise map_source_status(
                http_error.resp.status, connector=self.display_name
            ) from http_error
        except Exception as mail_error:
            self.logger.error(f"Failed to fetch mail content: {str(mail_error)}")
            raise to_stream_error(mail_error, connector=self.display_name) from mail_error

    async def _stream_attachment_record(
        self,
        gmail_service,
        file_id: str,
        record: Record,
        file_name: str,
        mime_type: str,
        convertTo: Optional[str] = None
    ) -> StreamingResponse:
        """
        Stream attachment content from Gmail, or from Drive for a Drive-hosted attachment.

        Args:
            gmail_service: Raw Gmail API service client
            file_id: Attachment ID, Drive file ID, or combined messageId~partId
            record: Record object
            file_name: Name of the file
            mime_type: MIME type of the file
            convertTo: Optional format to convert to (e.g., "application/pdf")

        Returns:
            StreamingResponse with attachment content
        """
        # Check if file_id is a Drive file ID (no tilde, typically longer alphanumeric)
        # Drive file IDs don't contain tildes, while our stable IDs use messageId~partId format
        is_drive_file = "~" not in file_id

        if is_drive_file:
            # This is a Drive file, use Drive API directly
            self.logger.info(f"Detected Drive file ID: {file_id}, using Drive API")
            return await self._stream_from_drive(file_id, record, file_name, mime_type, convertTo)

        # Get parent message record using parent_external_record_id
        message_id = None
        if record.parent_external_record_id:
            parent_record = await self.data_entities_processor.get_record_by_external_id(
                record.connector_id, record.parent_external_record_id
            )
            if parent_record:
                message_id = parent_record.external_record_id
                self.logger.info(f"Found parent message ID: {message_id} from parent_external_record_id")

        if not message_id:
            self.logger.error(f"Parent message ID not found for attachment record {record.id}")
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail="Parent message not found for attachment"
            )

        # Check if file_id is a combined ID (messageId~partId format)
        actual_attachment_id = file_id
        if "~" in file_id:
            file_message_id, part_id = file_id.split("~", 1)

            # Use the message_id from parent record, but validate it matches
            if file_message_id != message_id:
                self.logger.warning(
                    f"Message ID mismatch: file_id has {file_message_id}, parent has {message_id}. Using parent message_id."
                )

            # Fetch the message to get the actual attachment ID
            try:
                request = (
                    gmail_service.users()
                    .messages()
                    .get(userId="me", id=message_id, format="full")
                )
                message = await self._stream_call(request, "Gmail read")
            except HttpError as access_error:
                self.logger.error(f"Failed to fetch message {message_id}: {str(access_error)}")
                raise map_source_status(
                    access_error.resp.status, connector=self.display_name
                ) from access_error
            except Exception as access_error:
                self.logger.error(f"Failed to fetch message {message_id}: {str(access_error)}")
                raise to_stream_error(
                    access_error, connector=self.display_name
                ) from access_error

            if not message or "payload" not in message:
                self.logger.error(f"Message or payload not found for message ID {message_id}")
                raise not_found_at_source(self.display_name)

            # A `messageId~partId` id names a Gmail part only; Drive cannot resolve it.
            part = find_message_part(message["payload"].get("parts"), part_id)
            if part is None:
                self.logger.error(f"Part {part_id} not found in message {message_id}")
                raise not_found_at_source(self.display_name)
            actual_attachment_id = part.get("body", {}).get("attachmentId")
            if not actual_attachment_id:
                raise not_downloadable(
                    "This attachment has no downloadable content in Gmail.",
                    connector=self.display_name,
                )
            self.logger.info(f"Found attachment ID: {actual_attachment_id}")

        # Try to get the attachment from Gmail
        try:
            request = (
                gmail_service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=message_id, id=actual_attachment_id)
            )
            attachment = await self._stream_call(request, "Gmail read")

            # Decode the attachment data
            file_data = decode_body_data(attachment["data"])

            if convertTo == MimeTypes.PDF.value:
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_file_path = temp_path_for(temp_dir, file_name)

                    # Write attachment data to temp file
                    with open(temp_file_path, "wb") as f:
                        f.write(file_data)

                    # Convert to PDF
                    pdf_path = await self._convert_to_pdf(temp_file_path, temp_dir)
                    return create_stream_record_response(
                        open(pdf_path, "rb"),
                        filename=f"{Path(file_name).stem}",
                        mime_type="application/pdf",
                        fallback_filename=f"record_{record.id}"
                    )

            # Return original file if no conversion requested
            return create_stream_record_response(
                iter([file_data]),
                filename=f"{file_name}",
                mime_type="application/octet-stream",
                fallback_filename=f"record_{record.id}"
            )

        except HttpError as gmail_error:
            # Only `messageId~partId` ids reach here — a Drive id returned above.
            # Drive cannot resolve one, and its non-PDF path hands back a lazy
            # StreamingResponse that "succeeds" here and fails only once the
            # router pulls a chunk, turning Gmail's 401/429 into a Drive 404.
            self.logger.error(
                f"Failed to get Gmail attachment {file_id}: {str(gmail_error)}"
            )
            raise map_source_status(
                gmail_error.resp.status, connector=self.display_name
            ) from gmail_error
        except HTTPException:
            raise
        except Exception as attachment_error:
            self.logger.error(f"Error streaming attachment: {str(attachment_error)}")
            raise to_stream_error(
                attachment_error, connector=self.display_name
            ) from attachment_error

    async def stream_record(self, record: Record, convertTo: Optional[str] = None) -> StreamingResponse:
        """
        Stream a record from Google Gmail.

        Args:
            record: Record object containing file/message information
            convertTo: Optional format to convert to (e.g., "application/pdf")

        Returns:
            StreamingResponse with file/message content
        """

        try:
            file_id = record.external_record_id
            record_type = record.record_type

            if not file_id:
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail="File ID not found in record"
                )

            self.logger.info(f"Streaming Gmail record: {file_id}, type: {record_type}, convertTo: {convertTo}")

            # Check if gmail_data_source is initialized
            if not self.gmail_data_source:
                raise connector_not_ready(self.display_name)

            # Get raw Gmail service client
            gmail_service = self.gmail_data_source.client

            # Route to appropriate handler based on record type
            if record_type == RecordTypes.MAIL.value:
                return await self._stream_mail_record(gmail_service, file_id, record)
            else:
                # For attachments, get file metadata from record
                file_name = record.record_name or "attachment"
                mime_type = record.mime_type if hasattr(record, 'mime_type') and record.mime_type else "application/octet-stream"

                return await self._stream_attachment_record(
                    gmail_service, file_id, record, file_name, mime_type, convertTo
                )

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Error streaming record: {str(e)}", exc_info=True)
            raise to_stream_error(e, connector=self.display_name) from e

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
                app_name=self.connector_name,
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
                        inherit_permissions=True,
                    )

                    # The creator passes the app gate. An empty list clears an
                    # owner grant an older sync stored on the label.
                    await self.data_entities_processor.on_new_record_groups(
                        [(record_group, [])]
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

    async def _find_previous_message_in_thread(
        self,
        thread_id: str,
        current_message_id: str,
        current_internal_date: Optional[str],
        batch_records: Optional[List[Tuple[Record, List[Permission]]]] = None
    ) -> Optional[str]:
        """
        Find the previous message in a thread to create sibling relation.

        Args:
            thread_id: Thread ID
            current_message_id: Current message ID
            current_internal_date: Current message internal date (epoch milliseconds)
            batch_records: Optional list of records already processed in current batch

        Returns:
            Previous message's record ID if found, None otherwise
        """
        try:
            # Get full thread to see all messages
            await self._get_fresh_datasource()
            thread = await self.gmail_data_source.users_threads_get(
                userId="me",
                id=thread_id,
                format="full"
            )

            messages = thread.get('messages', [])
            min_messages = 2
            if not messages or len(messages) < min_messages:
                # No previous message if thread has less than 2 messages
                return None

            # Sort messages by internalDate to find chronological order
            current_date = int(current_internal_date) if current_internal_date else 0

            # Find messages that come before the current one
            previous_messages = []
            for msg in messages:
                msg_id = msg.get('id')
                if msg_id == current_message_id:
                    continue

                msg_date = int(msg.get('internalDate', 0))
                if msg_date < current_date:
                    previous_messages.append((msg_id, msg_date))

            if not previous_messages:
                return None

            # Get the most recent previous message (closest to current date)
            previous_messages.sort(key=lambda x: x[1], reverse=True)
            previous_message_id = previous_messages[0][0]

            # First check batch_records for messages processed in current batch
            if batch_records:
                for record, _ in batch_records:
                    if hasattr(record, 'external_record_id') and record.external_record_id == previous_message_id:
                        return record.id

            # Then check the database for existing records
            previous_record = await self._get_existing_record(previous_message_id)
            if previous_record:
                return previous_record.id

            return None

        except Exception as e:
            self.logger.warning(f"Error finding previous message in thread {thread_id}: {e}")
            return None

    async def run_sync(self) -> None:
        """Run sync for Google Gmail. A run that could not read part of the mailbox raises at the end, so it is
        not recorded as a success and a full sync removes nothing."""
        self._sync_health = GmailSyncHealth()
        self._call_policy = GmailCallPolicy(self.logger)
        user_email = "the connected mailbox"
        try:
            self.logger.info("Starting sync for Google Gmail Individual")
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "gmail", self.connector_id, self.logger
            )

            await self._get_fresh_datasource()
            user_profile = await self._call_policy.run(
                lambda: self.gmail_data_source.users_get_profile(userId="me"), "profile"
            )
            user_email = user_profile.get("emailAddress")
            if not user_email:
                raise ValueError("Email address not found in user profile")
            # The account this connection signs in as may differ from the creator's PipesHub address
            # (a personal Gmail account): link them, so the creator finds this mail.
            await self.register_authenticated_source_user(user_email, user_email)
            await self._create_app_user(user_profile)
            await self._create_personal_record_group(user_email)
            await self._sync_user_mailbox(user_email)
        except Exception as e:
            self._sync_health.record(user_email, e, "mailbox")
            self.logger.error(f"❌ Error during sync: {e}", exc_info=True)
            await self._report_sync_problems()
            raise
        await self._report_sync_problems()
        self._sync_health.raise_if_incomplete()
        self.logger.info("Sync completed for Google Gmail Individual")

    async def _report_sync_problems(self) -> None:
        if self._sync_health.problems:
            self.last_sync_summary = self._sync_health.summary()
        await GmailNotices(self.notify, self._notification_title, workspace=False).report(self._sync_health)

    def full_sync_coverage(self) -> FullSyncCoverage:
        return self._sync_health.coverage()

    async def run_incremental_sync(self) -> None:
        """Run incremental sync for Google Gmail."""
        self.logger.info("Starting incremental sync for Google Gmail Individual")
        await self.run_sync()

    def handle_webhook_notification(self, notification: Dict) -> None:
        """Handle webhook notifications from Google Gmail."""
        raise NotImplementedError("handle_webhook_notification is not yet implemented for Google Gmail")

    async def cleanup(self) -> None:
        """Cleanup resources when shutting down the connector."""
        try:
            self.logger.info("Cleaning up Google Gmail connector resources")

            await self._release_thread_lease()

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
        try:
            if not record_results:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(record_results)} Google Gmail records")

            if not self.gmail_data_source:
                self.logger.error("Gmail data source not initialized. Call init() first.")
                raise Exception("Gmail data source not initialized. Call init() first.")

            # Check records at source for updates
            org_id = self.data_entities_processor.org_id
            updated_records = []
            non_updated_records = []
            for record in record_results:
                try:
                    updated_record_data = await self._check_and_fetch_updated_record(org_id, record)
                    if updated_record_data:
                        updated_record, permissions = updated_record_data
                        updated_records.append((updated_record, permissions))
                    else:
                        non_updated_records.append(record)
                except Exception as e:
                    self.logger.error(f"Error checking record {record.id} at source: {e}")
                    continue

            # Update DB only for records that changed at source
            if updated_records:
                await self.data_entities_processor.on_new_records(updated_records, replace_permissions=True)
                self.logger.info(f"Updated {len(updated_records)} records in DB that changed at source")

            # Publish reindex events for non updated records
            if non_updated_records:
                await self.data_entities_processor.reindex_existing_records(non_updated_records)
                self.logger.info(f"Published reindex events for {len(non_updated_records)} non updated records")
        except Exception as e:
            self.logger.error(f"Error during Google Gmail reindex: {e}", exc_info=True)
            raise

    async def _check_and_fetch_updated_record(
        self, org_id: str, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch record from Gmail and return data for reindexing if changed."""
        try:
            external_record_id = record.external_record_id

            if not external_record_id:
                self.logger.warning(f"Missing external_record_id for record {record.id}")
                return None

            # Get user email from profile (individual connector has only one user)
            if not self.gmail_data_source:
                self.logger.error("Gmail data source not initialized. Call init() first.")
                return None

            try:
                await self._get_fresh_datasource()
                user_profile = await self.gmail_data_source.users_get_profile(userId="me")
                user_email = user_profile.get("emailAddress")
            except Exception as e:
                self.logger.error(f"Error getting user profile: {e}")
                return None

            if not user_email:
                self.logger.warning(f"Email address not found in user profile for record {record.id}")
                return None

            # Use self.gmail_data_source directly (no need to create user-specific client)
            # Route to appropriate handler based on record type
            record_type = record.record_type
            if record_type == RecordType.MAIL:
                return await self._check_and_fetch_updated_mail_record(
                    org_id, record, user_email
                )
            elif record_type == RecordType.FILE:
                return await self._check_and_fetch_updated_file_record(
                    org_id, record, user_email
                )
            else:
                self.logger.warning(f"Unknown record type {record_type} for record {record.id}")
                return None

        except Exception as e:
            self.logger.error(f"Error checking Google Gmail record {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_mail_record(
        self,
        org_id: str,
        record: Record,
        user_email: str
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch mail record from Gmail and return data for reindexing if changed."""
        try:
            message_id = record.external_record_id

            if not message_id:
                self.logger.warning(f"Missing message_id for record {record.id}")
                return None

            # Fetch fresh message from Gmail API
            try:
                message = await self.gmail_data_source.users_messages_get(
                    userId="me",
                    id=message_id,
                    format="full"
                )
            except HttpError as e:
                if e.resp.status == HttpStatusCode.NOT_FOUND.value:
                    self.logger.warning(f"Message {message_id} not found at source")
                    return None
                raise

            if not message:
                self.logger.warning(f"Message {message_id} not found at source")
                return None

            # Extract thread_id
            thread_id = message.get('threadId')
            if not thread_id:
                self.logger.warning(f"Message {message_id} has no threadId")
                return None

            # Find previous message in thread (optional)
            # Note: individual connector's _find_previous_message_in_thread has different signature
            previous_message_id = await self._find_previous_message_in_thread(
                thread_id,
                message_id,
                message.get('internalDate')
            )

            # Process message using existing function
            record_update = await self._process_gmail_message(
                user_email,
                message,
                thread_id,
                previous_message_id
            )

            if not record_update or record_update.is_deleted:
                return None

            # Only return data if there's an actual update (metadata, content, or permissions)
            if record_update.is_updated:
                self.logger.info(f"Record {message_id} has changed at source. Updating.")
                # Ensure we keep the internal DB ID
                record_update.record.id = record.id
                return (record_update.record, record_update.new_permissions)

            return None

        except Exception as e:
            self.logger.error(f"Error checking Google Gmail mail record {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_file_record(
        self,
        org_id: str,
        record: Record,
        user_email: str
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch file (attachment) record from Gmail and return data for reindexing if changed."""
        try:
            stable_attachment_id = record.external_record_id

            if not stable_attachment_id:
                self.logger.warning(f"Missing stable_attachment_id for record {record.id}")
                return None

            # Check if this is a Drive file (no tilde, typically longer alphanumeric)
            is_drive_file = "~" not in stable_attachment_id

            if is_drive_file:
                # For Drive files, we need to find the parent message to get permissions
                # Drive files are stored with driveFileId as external_record_id
                parent_message_id = record.parent_external_record_id
                if not parent_message_id:
                    self.logger.warning(f"Drive file {stable_attachment_id} has no parent message ID")
                    return None

                # Fetch parent message to get permissions
                try:
                    parent_message = await self.gmail_data_source.users_messages_get(
                        userId="me",
                        id=parent_message_id,
                        format="full"
                    )
                except HttpError as e:
                    if e.resp.status == HttpStatusCode.NOT_FOUND.value:
                        self.logger.warning(f"Parent message {parent_message_id} not found at source")
                        return None
                    raise

                if not parent_message:
                    self.logger.warning(f"Parent message {parent_message_id} not found at source")
                    return None

                # Extract attachment info from parent message
                attachment_infos = self._extract_attachment_infos(parent_message)

                # Find matching Drive attachment by driveFileId
                matching_attachment = None
                for attach_info in attachment_infos:
                    if attach_info.get('driveFileId') == stable_attachment_id:
                        matching_attachment = attach_info
                        break

                if not matching_attachment:
                    self.logger.warning(f"Drive attachment {stable_attachment_id} not found in parent message {parent_message_id}")
                    return None

                # Get parent mail permissions
                thread_id = parent_message.get('threadId')
                if not thread_id:
                    self.logger.warning(f"Parent message {parent_message_id} has no threadId")
                    return None

                previous_message_id = await self._find_previous_message_in_thread(
                    thread_id,
                    parent_message_id,
                    parent_message.get('internalDate')
                )

                parent_mail_update = await self._process_gmail_message(
                    user_email,
                    parent_message,
                    thread_id,
                    previous_message_id
                )

                parent_mail_permissions = []
                if parent_mail_update and parent_mail_update.new_permissions:
                    parent_mail_permissions = parent_mail_update.new_permissions

                if parent_mail_update and parent_mail_update.record:
                    external_record_group_id = parent_mail_update.record.external_record_group_id
                else:
                    external_record_group_id = self._mailbox_external_group_id(
                        user_email, parent_message.get("labelIds")
                    )
                # Process Drive attachment
                record_update = await self._process_gmail_attachment(
                    user_email,
                    parent_message_id,
                    matching_attachment,
                    parent_mail_permissions,
                    external_record_group_id,
                )

                if not record_update or record_update.is_deleted:
                    return None

                if record_update.is_updated:
                    self.logger.info(f"Drive file record {stable_attachment_id} has changed at source. Updating.")
                    record_update.record.id = record.id
                    return (record_update.record, record_update.new_permissions)

                return None

            # Regular attachment: Parse stableAttachmentId to get message_id and part_id
            # Format: message_id~partId
            try:
                message_id, part_id = stable_attachment_id.split("~", 1)
            except ValueError:
                self.logger.warning(f"Could not parse stable_attachment_id for record {record.id}: {stable_attachment_id}")
                return None

            # Get parent message ID (should match, but use from record if available)
            parent_message_id = record.parent_external_record_id
            if not parent_message_id:
                parent_message_id = message_id

            # Fetch parent message from Gmail API
            try:
                parent_message = await self.gmail_data_source.users_messages_get(
                    userId="me",
                    id=parent_message_id,
                    format="full"
                )
            except HttpError as e:
                if e.resp.status == HttpStatusCode.NOT_FOUND.value:
                    self.logger.warning(f"Parent message {parent_message_id} not found at source")
                    return None
                raise

            if not parent_message:
                self.logger.warning(f"Parent message {parent_message_id} not found at source")
                return None

            # Extract attachment info from parent message
            attachment_infos = self._extract_attachment_infos(parent_message)

            # Find matching attachment by stableAttachmentId
            matching_attachment = None
            for attach_info in attachment_infos:
                if attach_info.get('stableAttachmentId') == stable_attachment_id:
                    matching_attachment = attach_info
                    break

            if not matching_attachment:
                self.logger.warning(f"Attachment {stable_attachment_id} not found in parent message {parent_message_id}")
                return None

            # Get parent mail permissions by processing parent message first
            # Extract thread_id from parent message
            thread_id = parent_message.get('threadId')
            if not thread_id:
                self.logger.warning(f"Parent message {parent_message_id} has no threadId")
                return None

            # Find previous message in thread (optional)
            # Note: individual connector's _find_previous_message_in_thread has different signature
            previous_message_id = await self._find_previous_message_in_thread(
                thread_id,
                parent_message_id,
                parent_message.get('internalDate')
            )

            # Process parent message to get permissions
            parent_mail_update = await self._process_gmail_message(
                user_email,
                parent_message,
                thread_id,
                previous_message_id
            )

            # Get permissions from parent mail update, or use empty list
            parent_mail_permissions = []
            if parent_mail_update and parent_mail_update.new_permissions:
                parent_mail_permissions = parent_mail_update.new_permissions

            if parent_mail_update and parent_mail_update.record:
                external_record_group_id = parent_mail_update.record.external_record_group_id
            else:
                external_record_group_id = self._mailbox_external_group_id(
                    user_email, parent_message.get("labelIds")
                )
            # Process attachment using existing function
            record_update = await self._process_gmail_attachment(
                user_email,
                parent_message_id,
                matching_attachment,
                parent_mail_permissions,
                external_record_group_id,
            )

            if not record_update or record_update.is_deleted:
                return None

            # Only return data if there's an actual update (metadata, content, or permissions)
            if record_update.is_updated:
                self.logger.info(f"Record {stable_attachment_id} has changed at source. Updating.")
                # Ensure we keep the internal DB ID
                record_update.record.id = record.id
                return (record_update.record, record_update.new_permissions)

            return None

        except Exception as e:
            self.logger.error(f"Error checking Google Gmail file record {record.id} at source: {e}")
            return None

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
        connector_id: str,
        scope: str,
        created_by: str,
        data_entities_processor,
        **kwargs,
    ) -> BaseConnector:
        """Create a new instance of the Google Gmail connector."""
        return cls(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by
        )
