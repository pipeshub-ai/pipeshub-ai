import asyncio
import base64
import io
import os
import tempfile
import uuid
from logging import Logger
from pathlib import Path
from typing import AsyncGenerator, Dict, List, Optional, Tuple

import html2text
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload
from mailparser_reply import EmailReplyParser

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes,
    RecordRelations,
    RecordTypes,
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
from app.connectors.core.registry.auth_builder import AuthType, OAuthScopeConfig
from app.connectors.core.registry.connector_builder import (
    AuthBuilder,
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
)
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
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.models.entities import (
    AppUser,
    FileRecord,
    IndexingStatus,
    MailRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.google.google import GoogleClient
from app.sources.external.google.gmail.gmail import GoogleGmailDataSource
from app.utils.oauth_config import fetch_oauth_config_by_id
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import get_epoch_timestamp_in_ms


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
            GmailIndividualApp(connector_id),
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

        # Filter collections
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

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

    async def _get_existing_record(self, external_record_id: str) -> Optional[Record]:
        """Get existing record from data store."""
        try:
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=external_record_id
                )
                return existing_record
        except Exception as e:
            self.logger.error(f"Error getting existing record {external_record_id}: {e}")
            return None

    def _pass_date_filter(self, message: dict) -> bool:
        """
        Checks if the Gmail message passes the configured RECEIVED_DATE filter.
        Relies on client-side filtering since Gmail API does not support date filtering.
        """
        # Check Received Date Filter
        received_filter = self.sync_filters.get(SyncFilterKey.RECEIVED_DATE)
        if received_filter:
            # Get start and end timestamps in milliseconds directly
            start_ts = received_filter.get_datetime_start()
            end_ts = received_filter.get_datetime_end()

            # Extract internalDate from message (already in epoch milliseconds as string)
            internal_date = message.get("internalDate")
            if internal_date:
                try:
                    item_ts = int(internal_date)
                except (ValueError, TypeError):
                    # If we can't parse the date, skip the filter check
                    return True

                # Check if message is before start date
                if start_ts and item_ts < start_ts:
                    return False
                # Check if message is after end date
                if end_ts and item_ts > end_ts:
                    return False

        return True

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

            if not self._pass_date_filter(message):
                self.logger.debug(f"Skipping message {message_id} due to date filter")
                return None

            # Extract labelIds from message
            label_ids = message.get('labelIds', [])
            message.get('snippet', '')
            internal_date = message.get('internalDate')  # Epoch milliseconds as string

            # Determine external_record_group_id based on labelIds (SENT or INBOX)
            # Prefer SENT if both are present
            external_record_group_id = None
            if "SENT" in label_ids:
                external_record_group_id = f"{user_email}:SENT"
            elif "INBOX" in label_ids:
                external_record_group_id = f"{user_email}:INBOX"
            else:
                external_record_group_id = f"{user_email}:OTHERS"

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
                weburl=f"https://mail.google.com/mail?authuser={user_email}#all/{message_id}",
                preview_renderable=False,
                subject=subject,
                from_email=from_email,
                to_emails=to_emails,
                cc_emails=cc_emails,
                bcc_emails=bcc_emails,
                internet_message_id=internet_message_id,
            )

            # Extract sender email from "from" header (may contain name)
            sender_email = self._extract_email_from_header(from_email)

            # Create permission based on whether user_email is the sender
            permissions = []
            if user_email:
                # Normalize emails for comparison (case-insensitive)
                user_email_lower = user_email.lower()
                sender_email_lower = sender_email.lower() if sender_email else ""

                if sender_email_lower and user_email_lower == sender_email_lower:
                    # User is the sender - create owner permission
                    permissions.append(Permission(
                        email=user_email,
                        type=PermissionType.OWNER,
                        entity_type=EntityType.USER
                    ))
                else:
                    # User is not the sender - create read permission
                    permissions.append(Permission(
                        email=user_email,
                        type=PermissionType.READ,
                        entity_type=EntityType.USER
                    ))

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
                        message_update.record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

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

        def extract_attachments(parts_list) -> List[Dict]:
            """Recursively extract attachments from message parts."""
            attachments = []
            for part in parts_list:
                # Check if this part is an attachment
                if part.get('filename'):
                    body = part.get('body', {})
                    attachment_id = body.get('attachmentId')
                    part_id = part.get('partId', 'unknown')

                    if attachment_id:
                        # Construct stable ID using message_id + partId
                        stable_attachment_id = f"{message_id}_{part_id}"

                        attachments.append({
                            'attachmentId': attachment_id,  # Volatile - for downloading
                            'stableAttachmentId': stable_attachment_id,  # Stable - for record ID
                            'partId': part_id,
                            'filename': part.get('filename'),
                            'mimeType': part.get('mimeType', 'application/octet-stream'),
                            'size': body.get('size', 0)
                        })

                # Recursively check nested parts
                if part.get('parts'):
                    attachments.extend(extract_attachments(part.get('parts')))

            return attachments

        attachment_infos = extract_attachments(parts)
        return attachment_infos

    async def _process_gmail_attachment(
        self,
        user_email: str,
        message_id: str,
        attachment_info: Dict,
        parent_mail_permissions: List[Permission],
    ) -> Optional[RecordUpdate]:
        """
        Process a single Gmail attachment and create a FileRecord.

        Args:
            user_email: Email of the user who owns the message
            message_id: ID of the parent message
            attachment_info: Attachment metadata dict with attachmentId, filename, mimeType, size
            parent_mail_permissions: Permissions from parent mail (attachments inherit these)

        Returns:
            RecordUpdate object or None
        """
        try:
            attachment_id = attachment_info.get('attachmentId')
            filename = attachment_info.get('filename', 'unnamed_attachment')
            mime_type = attachment_info.get('mimeType', 'application/octet-stream')
            size = attachment_info.get('size', 0)
            stable_attachment_id = attachment_info.get('stableAttachmentId')

            if not attachment_id or not stable_attachment_id:
                return None

            # Check for existing record
            existing_record = await self._get_existing_record(stable_attachment_id)
            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False  # Gmail attachments are immutable

            # Extract file extension from filename
            extension = None
            if '.' in filename:
                extension = filename.rsplit('.', 1)[-1].lower()

            record_id = existing_record.id if existing_record else str(uuid.uuid4())

            # Create FileRecord
            file_record = FileRecord(
                id=record_id,
                org_id=self.data_entities_processor.org_id,
                record_name=filename,
                record_type=RecordType.FILE,
                record_group_type=RecordGroupType.MAILBOX,
                external_record_id=stable_attachment_id,
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
                weburl=f"https://mail.google.com/mail?authuser={user_email}#all/{message_id}",
                size_in_bytes=size,
                extension=extension,
                is_file=True,
                is_dependent_node=True,
            )

            # Check indexing filter for attachments
            if not self.indexing_filters.is_enabled(IndexingFilterKey.ATTACHMENTS, default=True):
                file_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

            # Inherit parent mail permissions
            attachment_permissions = parent_mail_permissions

            self.logger.debug(
                f"Processed attachment {attachment_id}: {filename} ({size} bytes)"
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
            self.logger.error(f"Error processing Gmail attachment {attachment_info.get('attachmentId', 'unknown')}: {e}")
            return None

    async def _process_gmail_attachment_generator(
        self,
        user_email: str,
        message_id: str,
        attachment_infos: List[Dict],
        parent_mail_permissions: List[Permission]
    ) -> AsyncGenerator[Optional[RecordUpdate], None]:
        """
        Process Gmail attachments and yield RecordUpdate objects.
        Generator for non-blocking processing of large datasets.

        Args:
            user_email: Email of the user who owns the message
            message_id: ID of the parent message
            attachment_infos: List of attachment metadata dictionaries
            parent_mail_permissions: Permissions from parent mail (attachments inherit these)
        """
        for attach_info in attachment_infos:
            try:
                attach_update = await self._process_gmail_attachment(
                    user_email,
                    message_id,
                    attach_info,
                    parent_mail_permissions
                )

                if attach_update:
                    if attach_update.record and not self.indexing_filters.is_enabled(IndexingFilterKey.ATTACHMENTS, default=True):
                        attach_update.record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                    yield attach_update

                # Allow other tasks to run
                await asyncio.sleep(0)

            except Exception as e:
                self.logger.error(f"Error processing attachment in generator: {e}", exc_info=True)
                continue

    async def _sync_user_mailbox(self) -> None:
        """
        Synchronizes Gmail mailbox contents for the current user.
        Routes to incremental sync if history_id exists, otherwise performs full sync.
        """
        try:
            if not self.gmail_data_source:
                self.logger.error("Gmail data source not initialized")
                return

            # Load sync and indexing filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "gmail", self.connector_id, self.logger
            )

            # Get user profile to extract email
            user_profile = await self.gmail_data_source.users_get_profile(userId="me")
            user_email = user_profile.get("emailAddress")
            if not user_email:
                self.logger.error("Email address not found in user profile")
                return

            self.logger.info(f"Starting sync for user {user_email}")

            # Get sync point for this user
            sync_point_key = generate_record_sync_point_key(RecordType.MAIL.value, "user", user_email)
            sync_point = await self.gmail_delta_sync_point.read_sync_point(sync_point_key)

            # Check if history_id exists for incremental sync
            history_id = sync_point.get('historyId') if sync_point else None

            if history_id:
                self.logger.info(f"History ID found for user {user_email}, performing incremental sync")
                try:
                    await self._run_sync_with_history_id(user_email, history_id, sync_point_key)
                except Exception as e:
                    # If incremental sync fails, fallback to full sync
                    self.logger.warning(
                        f"Incremental sync failed for user {user_email}, "
                        f"falling back to full sync: {e}"
                    )
                    await self._run_full_sync(user_email, sync_point_key)
            else:
                self.logger.info(f"No history ID found for user {user_email}, performing full sync")
                await self._run_full_sync(user_email, sync_point_key)

        except Exception as ex:
            self.logger.error(f"❌ Error in sync for user: {ex}")
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
        """
        Helper function to convert file to PDF using LibreOffice.

        Args:
            file_path: Path to the file to convert
            temp_dir: Temporary directory for output

        Returns:
            Path to the converted PDF file
        """
        pdf_path = os.path.join(temp_dir, f"{Path(file_path).stem}.pdf")

        try:
            conversion_cmd = [
                "soffice",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                temp_dir,
                file_path,
            ]
            process = await asyncio.create_subprocess_exec(
                *conversion_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Add timeout to communicate
            try:
                conversion_output, conversion_error = await asyncio.wait_for(
                    process.communicate(), timeout=30.0
                )
            except asyncio.TimeoutError:
                # Make sure to terminate the process if it times out
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    process.kill()  # Force kill if termination takes too long
                self.logger.error("LibreOffice conversion timed out after 30 seconds")
                raise HTTPException(
                    status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                    detail="PDF conversion timed out"
                )

            if process.returncode != 0:
                error_msg = f"LibreOffice conversion failed: {conversion_error.decode('utf-8', errors='replace')}"
                self.logger.error(error_msg)
                raise HTTPException(
                    status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                    detail="Failed to convert file to PDF"
                )

            if os.path.exists(pdf_path):
                return pdf_path
            else:
                raise HTTPException(
                    status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                    detail="PDF conversion failed - output file not found"
                )
        except asyncio.TimeoutError:
            # This catch is for any other timeout that might occur
            self.logger.error("Timeout during PDF conversion")
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail="PDF conversion timed out"
            )
        except Exception as conv_error:
            self.logger.error(f"Error during conversion: {str(conv_error)}")
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail="Error converting file to PDF"
            )

    async def _stream_mail_record(
        self,
        gmail_service,
        message_id: str,
        record: Record
    ) -> StreamingResponse:
        try:
            # 1. Fetch message
            message = (
                gmail_service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )

            # 2. Extract payload (HTML)
            mail_content_base64 = self._extract_body_from_payload(message.get("payload", {}))
            raw_html = base64.urlsafe_b64decode(
                mail_content_base64.encode("ASCII")
            ).decode("utf-8", errors="replace")

            latest_reply_text = ""

            if raw_html:
                # --- STEP 1: Smart Conversion (HTML -> Text) ---
                converter = html2text.HTML2Text()
                converter.ignore_links = False
                converter.ignore_images = False
                converter.body_width = 0

                clean_text = converter.handle(raw_html)

                # --- STEP 2: Extract Reply ---
                email_parser = EmailReplyParser(languages=['en'])
                parsed_mail = email_parser.read(clean_text)

                latest_reply_text = parsed_mail.latest_reply

                if not latest_reply_text:
                    latest_reply_text = clean_text

            async def message_stream() -> AsyncGenerator[bytes, None]:
                yield latest_reply_text.encode("utf-8")

            return create_stream_record_response(
                message_stream(),
                filename=f"{record.record_name}",
                mime_type="text/plain",
                fallback_filename=f"record_{record.id}"
            )

        except HttpError as http_error:
            if hasattr(http_error, 'resp') and http_error.resp.status == HttpStatusCode.NOT_FOUND.value:
                self.logger.error(f"Message not found with ID {message_id}")
                raise HTTPException(
                    status_code=HttpStatusCode.NOT_FOUND.value,
                    detail="Message not found"
                )
            self.logger.error(f"Failed to fetch mail content: {str(http_error)}")
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail="Failed to fetch mail content"
            )
        except Exception as mail_error:
            self.logger.error(f"Failed to fetch mail content: {str(mail_error)}")
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail="Failed to fetch mail content"
            )

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
        Stream attachment content from Gmail with Drive fallback.

        Args:
            gmail_service: Raw Gmail API service client
            file_id: Attachment ID or combined messageId_partId
            record: Record object
            file_name: Name of the file
            mime_type: MIME type of the file
            convertTo: Optional format to convert to (e.g., "application/pdf")

        Returns:
            StreamingResponse with attachment content
        """
        # Get parent message record using parent_external_record_id
        message_id = None
        if record.parent_external_record_id:
            async with self.data_store_provider.transaction() as tx_store:
                parent_record = await tx_store.get_record_by_external_id(
                    connector_id=record.connector_id,
                    external_id=record.parent_external_record_id
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

        # Check if file_id is a combined ID (messageId_partId format)
        actual_attachment_id = file_id
        if "_" in file_id:
            try:
                file_message_id, part_id = file_id.split("_", 1)

                # Use the message_id from parent record, but validate it matches
                if file_message_id != message_id:
                    self.logger.warning(
                        f"Message ID mismatch: file_id has {file_message_id}, parent has {message_id}. Using parent message_id."
                    )

                # Fetch the message to get the actual attachment ID
                try:
                    message = (
                        gmail_service.users()
                        .messages()
                        .get(userId="me", id=message_id, format="full")
                        .execute()
                    )
                except HttpError as access_error:
                    if hasattr(access_error, 'resp') and access_error.resp.status == HttpStatusCode.NOT_FOUND.value:
                        self.logger.error(f"Message not found with ID {message_id}")
                        raise HTTPException(
                            status_code=HttpStatusCode.NOT_FOUND.value,
                            detail="Message not found"
                        )
                    raise access_error

                if not message or "payload" not in message:
                    raise Exception(f"Message or payload not found for message ID {message_id}")

                # Search for the part with matching partId
                parts = message["payload"].get("parts", [])
                for part in parts:
                    if part.get("partId") == part_id:
                        actual_attachment_id = part.get("body", {}).get("attachmentId")
                        if not actual_attachment_id:
                            raise Exception("Attachment ID not found in part body")
                        self.logger.info(f"Found attachment ID: {actual_attachment_id}")
                        break
                else:
                    raise Exception("Part ID not found in message")

            except Exception as e:
                self.logger.error(f"Error extracting attachment ID: {str(e)}")
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail=f"Invalid attachment ID format: {str(e)}"
                )

        # Try to get the attachment from Gmail
        try:
            attachment = (
                gmail_service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=message_id, id=actual_attachment_id)
                .execute()
            )

            # Decode the attachment data
            file_data = base64.urlsafe_b64decode(attachment["data"])

            if convertTo == MimeTypes.PDF.value:
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_file_path = os.path.join(temp_dir, file_name)

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
            self.logger.info(
                f"Failed to get attachment from Gmail: {str(gmail_error)}, trying Drive..."
            )

            # Try Drive as fallback
            try:
                # Get credentials from config for Drive service
                if not self.config or "credentials" not in self.config:
                    raise HTTPException(
                        status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                        detail="Credentials not available for Drive fallback"
                    )

                # Build Drive service with same credentials
                # Note: This assumes the same credentials can access Drive
                # You may need to adjust this based on your credential structure
                from google.oauth2 import service_account
                credentials_json = self.config.get("credentials", {}).get("auth", {})
                if not credentials_json:
                    raise HTTPException(
                        status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                        detail="Service account credentials not found for Drive fallback"
                    )

                credentials = service_account.Credentials.from_service_account_info(
                    credentials_json
                )
                drive_service = build("drive", "v3", credentials=credentials)

                if convertTo == MimeTypes.PDF.value:
                    with tempfile.TemporaryDirectory() as temp_dir:
                        temp_file_path = os.path.join(temp_dir, file_name)

                        # Download from Drive to temp file
                        with open(temp_file_path, "wb") as f:
                            request = drive_service.files().get_media(
                                fileId=file_id
                            )
                            downloader = MediaIoBaseDownload(f, request)

                            done = False
                            while not done:
                                status, done = downloader.next_chunk()
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
                    try:
                        request = drive_service.files().get_media(
                            fileId=file_id
                        )
                        buffer = io.BytesIO()
                        downloader = MediaIoBaseDownload(buffer, request)

                        done = False
                        while not done:
                            try:
                                status, done = downloader.next_chunk()
                                self.logger.info(
                                    f"Download {int(status.progress() * 100)}%."
                                )
                            except Exception as chunk_error:
                                self.logger.error(f"Error downloading chunk: {str(chunk_error)}")
                                raise

                        buffer.seek(0)
                        content = buffer.read()
                        if content:
                            yield content
                    except Exception as stream_error:
                        self.logger.error(f"Error in file stream: {str(stream_error)}")
                        raise HTTPException(
                            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                            detail="Error streaming file from Drive"
                        )
                    finally:
                        buffer.close()

                return create_stream_record_response(
                    file_stream(),
                    filename=file_name,
                    mime_type=mime_type,
                    fallback_filename=f"record_{record.id}"
                )

            except Exception as drive_error:
                self.logger.error(
                    f"Failed to get file from both Gmail and Drive. Gmail error: {str(gmail_error)}, Drive error: {str(drive_error)}"
                )
                raise HTTPException(
                    status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                    detail="Failed to download file from both Gmail and Drive",
                )
        except Exception as attachment_error:
            self.logger.error(f"Error streaming attachment: {str(attachment_error)}")
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail=f"Error streaming attachment: {str(attachment_error)}"
            )

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
                raise HTTPException(
                    status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                    detail="Gmail client not initialized"
                )

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
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail=f"Error streaming record: {str(e)}"
            )

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

    async def _run_full_sync(
        self,
        user_email: str,
        sync_point_key: str
    ) -> None:
        """
        Performs a full sync of Gmail mailbox contents for the current user.

        Args:
            user_email: The user email address
            sync_point_key: Sync point key for this user
        """
        try:
            self.logger.info(f"Starting full sync for user {user_email}")

            # Get user profile to extract historyId
            try:
                profile = await self.gmail_data_source.users_get_profile(userId="me")
                history_id = profile.get('historyId')
                self.logger.info(f"Retrieved historyId {history_id} for user {user_email}")
            except Exception as e:
                self.logger.warning(f"Failed to get historyId for user {user_email}: {e}")
                history_id = None

            # Initialize batch processing
            batch_records = []
            batch_count = 0
            total_threads = 0
            total_messages = 0
            page_token = None

            # Fetch threads with pagination
            while True:
                try:
                    # Fetch threads list
                    threads_response = await self.gmail_data_source.users_threads_list(
                        userId="me",
                        maxResults=100,
                        pageToken=page_token
                    )

                    threads = threads_response.get('threads', [])
                    if not threads:
                        break

                    self.logger.info(f"Fetched {len(threads)} threads for user {user_email}")
                    total_threads += len(threads)

                    # Process each thread
                    for thread_data in threads:
                        thread_id = thread_data.get('id')
                        if not thread_id:
                            continue

                        try:
                            # Get full thread with all messages
                            thread = await self.gmail_data_source.users_threads_get(
                                userId="me",
                                id=thread_id,
                                format="full"
                            )

                            messages = thread.get('messages', [])
                            if not messages:
                                continue

                            # Process messages in thread sequentially (ascending order) using generator
                            previous_message_id = None

                            async for mail_update in self._process_gmail_message_generator(
                                messages,
                                user_email,
                                thread_id
                            ):
                                try:
                                    if not mail_update or not mail_update.record:
                                        continue

                                    mail_record = mail_update.record
                                    permissions = mail_update.new_permissions or []

                                    # Add email to batch
                                    batch_records.append((mail_record, permissions))
                                    batch_count += 1
                                    total_messages += 1

                                    # Create SIBLING relation if there was a previous message
                                    if previous_message_id:
                                        try:
                                            async with self.data_store_provider.transaction() as tx_store:
                                                await tx_store.create_record_relation(
                                                    previous_message_id,
                                                    mail_record.id,
                                                    RecordRelations.SIBLING.value
                                                )
                                        except Exception as relation_error:
                                            self.logger.error(f"Error creating sibling relation: {relation_error}")

                                    # Update previous message ID
                                    previous_message_id = mail_record.id

                                    # Extract attachment_infos from message payload
                                    message_id = mail_record.external_record_id
                                    # Find the message in the messages list to extract attachment info
                                    message = None
                                    for msg in messages:
                                        if msg.get('id') == message_id:
                                            message = msg
                                            break

                                    if message:
                                        attachment_infos = self._extract_attachment_infos(message)

                                        # Process attachments using generator
                                        async for attach_update in self._process_gmail_attachment_generator(
                                            user_email,
                                            message_id,
                                            attachment_infos,
                                            permissions
                                        ):
                                            if attach_update and attach_update.record:
                                                # Add attachment to SAME batch_records list
                                                batch_records.append((attach_update.record, attach_update.new_permissions or []))
                                                batch_count += 1

                                    # Process batch when it reaches the size limit
                                    if batch_count >= self.batch_size:
                                        await self.data_entities_processor.on_new_records(batch_records)
                                        self.logger.info(f"Processed batch of {batch_count} records for user {user_email}")
                                        batch_records = []
                                        batch_count = 0

                                        # Allow other operations to proceed
                                        await asyncio.sleep(0.1)

                                except Exception as msg_error:
                                    self.logger.error(f"Error processing message: {msg_error}")
                                    continue

                        except Exception as thread_error:
                            self.logger.error(f"Error processing thread {thread_id}: {thread_error}")
                            continue

                    # Check for next page
                    next_page_token = threads_response.get('nextPageToken')
                    if next_page_token:
                        page_token = next_page_token

                        # Save intermediate pageToken for resumability
                        await self.gmail_delta_sync_point.update_sync_point(
                            sync_point_key,
                            {
                                "pageToken": page_token,
                                "historyId": history_id
                            }
                        )
                    else:
                        # No more pages
                        break

                except Exception as page_error:
                    self.logger.error(f"Error fetching threads page: {page_error}")
                    raise

            # Process remaining records in batch
            if batch_records:
                await self.data_entities_processor.on_new_records(batch_records)
                self.logger.info(f"Processed final batch of {batch_count} records for user {user_email}")

            # Update sync point with final state (clear pageToken, keep historyId)
            await self.gmail_delta_sync_point.update_sync_point(
                sync_point_key,
                {
                    "pageToken": None,
                    "historyId": history_id,
                    "lastSyncTimestamp": get_epoch_timestamp_in_ms()
                }
            )

            self.logger.info(
                f"Completed full sync for user {user_email}: "
                f"{total_threads} threads, {total_messages} messages"
            )

        except Exception as ex:
            self.logger.error(f"❌ Error in full sync for user {user_email}: {ex}")
            raise

    def _merge_history_changes(self, inbox_changes: Dict, sent_changes: Dict) -> Dict:
        """
        Merge and deduplicate history changes from multiple labels.

        Args:
            inbox_changes: History changes from INBOX label
            sent_changes: History changes from SENT label

        Returns:
            Merged history changes dictionary
        """
        merged_history = []
        seen_history_ids = set()

        for change in inbox_changes.get('history', []) + sent_changes.get('history', []):
            history_id = change.get('id')
            if history_id and history_id not in seen_history_ids:
                seen_history_ids.add(history_id)
                merged_history.append(change)

        # Sort by history ID to maintain chronological order
        merged_history.sort(key=lambda x: int(x.get('id', 0)))

        return {'history': merged_history}

    async def _fetch_history_changes(
        self,
        start_history_id: str,
        label_id: str
    ) -> Dict:
        """
        Fetch history changes for a specific label with pagination.

        Args:
            start_history_id: History ID to start from
            label_id: Label ID to filter by (e.g., "INBOX", "SENT")

        Returns:
            Dictionary containing history changes
        """
        all_history = []
        current_page_token = None

        while True:
            try:
                history_response = await self.gmail_data_source.users_history_list(
                    userId="me",
                    startHistoryId=start_history_id,
                    labelId=label_id,
                    historyTypes=["messageAdded", "messageDeleted", "labelAdded"],
                    maxResults=500,
                    pageToken=current_page_token
                )

                history_entries = history_response.get('history', [])
                if history_entries:
                    all_history.extend(history_entries)

                # Check for next page
                next_page_token = history_response.get('nextPageToken')
                if next_page_token:
                    current_page_token = next_page_token
                else:
                    break

            except HttpError:
                # Re-raise HttpError (especially 404) to be handled by caller
                raise
            except Exception as e:
                self.logger.error(f"Error fetching history changes for label {label_id}: {e}")
                raise

        return {'history': all_history}

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

    async def _delete_message_and_attachments(self, record_id: str, message_id: str) -> None:
        """
        Delete a message record and its associated attachments.

        Args:
            record_id: Internal record ID
            message_id: External message ID
        """
        try:
            # Find and delete associated attachment records first
            async with self.data_store_provider.transaction() as tx_store:
                # Get all attachment records with this message as parent
                attachment_records = await tx_store.get_records_by_parent(
                    connector_id=self.connector_id,
                    parent_external_record_id=message_id,
                    record_type=RecordTypes.FILE.value
                )

                # Delete each attachment record
                for attachment_record in attachment_records:
                    try:
                        await self.data_entities_processor.on_record_deleted(attachment_record.id)
                        self.logger.debug(f"Deleted attachment record {attachment_record.id} for message {message_id}")
                    except Exception as attach_error:
                        self.logger.error(f"Error deleting attachment {attachment_record.id}: {attach_error}")

            # Delete the main message record
            await self.data_entities_processor.on_record_deleted(record_id)
            self.logger.debug(f"Deleted message record {record_id} for message {message_id}")

        except Exception as e:
            self.logger.error(f"Error deleting message and attachments {message_id}: {e}")

    async def _process_history_changes(
        self,
        user_email: str,
        history_entry: Dict,
        batch_records: List[Tuple[Record, List[Permission]]]
    ) -> int:
        """
        Process a single history change entry.

        Args:
            user_email: The user email address
            history_entry: History change entry from Gmail API
            batch_records: List to append processed records to

        Returns:
            Number of records processed
        """
        records_processed = 0
        seen_message_ids = set()

        try:
            # Handle message additions
            messages_to_add = []
            if "messagesAdded" in history_entry:
                for message_added in history_entry["messagesAdded"]:
                    message = message_added.get("message", {})
                    message_id = message.get("id")
                    if message_id and message_id not in seen_message_ids:
                        seen_message_ids.add(message_id)
                        messages_to_add.append(message)

            # Handle labels added (messages moved to INBOX/SENT)
            if "labelsAdded" in history_entry:
                for label_added in history_entry["labelsAdded"]:
                    message = label_added.get("message", {})
                    message_id = message.get("id")
                    label_ids = label_added.get("labelIds", [])
                    # Only process if message is being added to INBOX or SENT and not already seen
                    if message_id and message_id not in seen_message_ids:
                        if any(label in ["INBOX", "SENT"] for label in label_ids):
                            seen_message_ids.add(message_id)
                            messages_to_add.append(message)

            # Process message additions
            for message in messages_to_add:
                try:
                    message_id = message.get("id")
                    if not message_id:
                        continue

                    # Check if message already exists
                    existing_record = await self._get_existing_record(message_id)
                    if existing_record:
                        self.logger.debug(f"Message {message_id} already exists, skipping")
                        continue

                    # Fetch full message details
                    try:
                        full_message = await self.gmail_data_source.users_messages_get(
                            userId="me",
                            id=message_id,
                            format="full"
                        )
                    except HttpError as http_error:
                        if hasattr(http_error, 'resp') and http_error.resp.status == HttpStatusCode.NOT_FOUND.value:
                            self.logger.warning(f"Message {message_id} not found, may have been deleted")
                        else:
                            self.logger.error(f"Error fetching message {message_id}: {http_error}")
                        continue
                    except Exception as fetch_error:
                        self.logger.error(f"Error fetching message {message_id}: {fetch_error}")
                        continue

                    if not full_message:
                        self.logger.warning(f"Failed to fetch full message {message_id}")
                        continue

                    # Extract thread_id
                    thread_id = full_message.get("threadId")
                    if not thread_id:
                        self.logger.warning(f"Message {message_id} has no threadId")
                        continue

                    # Get previous message in thread for sibling relation
                    previous_message_record_id = await self._find_previous_message_in_thread(
                        thread_id,
                        message_id,
                        full_message.get("internalDate"),
                        batch_records
                    )

                    # Process message using existing function
                    mail_update = await self._process_gmail_message(
                        user_email,
                        full_message,
                        thread_id,
                        previous_message_record_id
                    )

                    if mail_update and mail_update.record:
                        mail_record = mail_update.record
                        permissions = mail_update.new_permissions or []

                        if not self.indexing_filters.is_enabled(IndexingFilterKey.MAILS, default=True):
                            mail_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                        # Create SIBLING relation if there was a previous message
                        if previous_message_record_id:
                            try:
                                async with self.data_store_provider.transaction() as tx_store:
                                    await tx_store.create_record_relation(
                                        previous_message_record_id,
                                        mail_record.id,
                                        RecordRelations.SIBLING.value
                                    )
                            except Exception as relation_error:
                                self.logger.error(f"Error creating sibling relation: {relation_error}")

                        # Add to batch
                        batch_records.append((mail_record, permissions))
                        records_processed += 1

                        # Extract and process attachments
                        attachment_infos = self._extract_attachment_infos(full_message)
                        if attachment_infos:
                            async for attach_update in self._process_gmail_attachment_generator(
                                user_email,
                                message_id,
                                attachment_infos,
                                permissions
                            ):
                                if attach_update and attach_update.record:
                                    batch_records.append((
                                        attach_update.record,
                                        attach_update.new_permissions or []
                                    ))
                                    records_processed += 1

                except Exception as msg_error:
                    self.logger.error(f"Error processing message addition: {msg_error}")
                    continue

            # Handle message deletions
            messages_to_delete = []
            seen_message_ids = set()  # Reset for deletions
            if "messagesDeleted" in history_entry:
                for message_deleted in history_entry["messagesDeleted"]:
                    message = message_deleted.get("message", {})
                    message_id = message.get("id")
                    if message_id and message_id not in seen_message_ids:
                        seen_message_ids.add(message_id)
                        messages_to_delete.append(message)

            # Handle labels added with TRASH (messages moved to trash)
            if "labelsAdded" in history_entry:
                for label_added in history_entry["labelsAdded"]:
                    message = label_added.get("message", {})
                    message_id = message.get("id")
                    label_ids = label_added.get("labelIds", [])
                    if "TRASH" in label_ids and message_id and message_id not in seen_message_ids:
                        seen_message_ids.add(message_id)
                        messages_to_delete.append(message)

            # Process message deletions
            for message in messages_to_delete:
                try:
                    message_id = message.get("id")
                    if not message_id:
                        continue

                    # Find existing record
                    existing_record = await self._get_existing_record(message_id)
                    if not existing_record:
                        self.logger.debug(f"Message {message_id} not found in database, skipping deletion")
                        continue

                    # Delete the record and its attachments
                    await self._delete_message_and_attachments(existing_record.id, message_id)
                    records_processed += 1

                except Exception as delete_error:
                    self.logger.error(f"Error processing message deletion: {delete_error}")
                    continue

        except Exception as e:
            self.logger.error(f"Error processing history change: {e}", exc_info=True)

        return records_processed

    async def _run_sync_with_history_id(
        self,
        user_email: str,
        start_history_id: str,
        sync_point_key: str
    ) -> None:
        """
        Performs an incremental sync of Gmail mailbox contents using history API.

        Args:
            user_email: The user email address
            start_history_id: History ID to start from
            sync_point_key: Sync point key for this user
        """
        try:
            self.logger.info(f"Starting incremental sync for user {user_email} from historyId {start_history_id}")

            # Initialize batch processing
            batch_records = []
            batch_count = 0
            total_changes = 0
            latest_history_id = start_history_id

            # Fetch history changes for both INBOX and SENT labels
            # Process INBOX first
            try:
                inbox_changes = await self._fetch_history_changes(
                    start_history_id,
                    "INBOX"
                )
            except Exception as inbox_error:
                self.logger.error(f"Error fetching INBOX history changes: {inbox_error}")
                inbox_changes = {'history': []}

            # Process SENT changes
            try:
                sent_changes = await self._fetch_history_changes(
                    start_history_id,
                    "SENT"
                )
            except Exception as sent_error:
                self.logger.error(f"Error fetching SENT history changes: {sent_error}")
                sent_changes = {'history': []}

            # Combine and deduplicate changes
            all_changes = self._merge_history_changes(inbox_changes, sent_changes)

            # Process all history changes
            for history_entry in all_changes.get('history', []):
                try:
                    processed = await self._process_history_changes(
                        user_email,
                        history_entry,
                        batch_records
                    )
                    if processed:
                        batch_count += processed
                        total_changes += 1

                        # Process batch when it reaches the size limit
                        if batch_count >= self.batch_size:
                            await self.data_entities_processor.on_new_records(batch_records)
                            self.logger.info(f"Processed batch of {batch_count} records for user {user_email}")
                            batch_records = []
                            batch_count = 0

                            # Allow other operations to proceed
                            await asyncio.sleep(0.1)

                except Exception as change_error:
                    self.logger.error(f"Error processing history change: {change_error}")
                    continue

                # Update latest history ID from the entry
                if history_entry.get('id'):
                    latest_history_id = history_entry.get('id')

            # Process remaining records in batch
            if batch_records:
                try:
                    await self.data_entities_processor.on_new_records(batch_records)
                    self.logger.info(f"Processed final batch of {batch_count} records for user {user_email}")
                except Exception as batch_error:
                    self.logger.error(f"Error processing final batch: {batch_error}")

            # Get latest historyId from user profile if available
            try:
                profile = await self.gmail_data_source.users_get_profile(userId="me")
                current_history_id = profile.get('historyId')
                if current_history_id:
                    latest_history_id = current_history_id
            except Exception as profile_error:
                self.logger.warning(f"Failed to get current historyId from profile: {profile_error}")

            # Update sync point with new historyId (even on partial failures)
            try:
                await self.gmail_delta_sync_point.update_sync_point(
                    sync_point_key,
                    {
                        "pageToken": None,
                        "historyId": latest_history_id,
                        "lastSyncTimestamp": get_epoch_timestamp_in_ms()
                    }
                )
            except Exception as sync_point_error:
                self.logger.error(f"Error updating sync point: {sync_point_error}")

            self.logger.info(
                f"Completed incremental sync for user {user_email}: "
                f"{total_changes} changes processed, latest historyId: {latest_history_id}"
            )

        except HttpError:
            # Re-raise HttpError to be handled by caller (for 404 fallback)
            raise
        except Exception as ex:
            self.logger.error(f"❌ Error in incremental sync for user {user_email}: {ex}")
            # Try to update sync point even on error
            try:
                await self.gmail_delta_sync_point.update_sync_point(
                    sync_point_key,
                    {
                        "pageToken": None,
                        "historyId": start_history_id,  # Keep original on error
                        "lastSyncTimestamp": get_epoch_timestamp_in_ms()
                    }
                )
            except Exception:
                pass  # Ignore sync point update errors during error handling
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
        await self.run_sync()

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
                await self.data_entities_processor.on_new_records(updated_records)
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

            # Parse stableAttachmentId to get message_id and part_id
            # Format: message_id_partId
            if "_" not in stable_attachment_id:
                self.logger.warning(f"Invalid stable_attachment_id format for record {record.id}: {stable_attachment_id}")
                return None

            try:
                message_id, part_id = stable_attachment_id.split("_", 1)
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

            # Process attachment using existing function
            record_update = await self._process_gmail_attachment(
                user_email,
                parent_message_id,
                matching_attachment,
                parent_mail_permissions
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
