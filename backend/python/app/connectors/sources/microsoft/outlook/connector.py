import asyncio
import base64
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from logging import Logger
from typing import AsyncGenerator, Dict, List, Optional

from aiolimiter import AsyncLimiter
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
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
from app.connectors.sources.microsoft.common.apps import OutlookApp
from app.connectors.sources.microsoft.common.msgraph_client import (
    RecordUpdate,
)
from app.models.entities import (
    AppUser,
    FileRecord,
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
)
from app.sources.external.microsoft.users_groups.users_groups import (
    UsersGroupsDataSource,
    UsersGroupsResponse,
)


@dataclass
class OutlookCredentials:
    tenant_id: str
    client_id: str
    client_secret: str
    has_admin_consent: bool = False


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

        self.email_delta_sync_point = SyncPoint(
            connector_name=Connectors.OUTLOOK,
            org_id=self.data_entities_processor.org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=self.data_store_provider
        )

    async def init(self) -> bool:
        """Initialize the Outlook connector with credentials and Graph client."""
        try:

            org_id = self.data_entities_processor.org_id

            # Load credentials
            self.credentials = await self._get_credentials(org_id)

            # Create shared MSGraph client
            external_client: ExternalMSGraphClient = ExternalMSGraphClient.build_with_config(
                MSGraphClientWithClientIdSecretConfig(
                    self.credentials.client_id,
                    self.credentials.client_secret,
                    self.credentials.tenant_id
                ),
                mode=GraphMode.APP
            )

            # Create both data source clients
            self.external_outlook_client = OutlookCalendarContactsDataSource(external_client)
            self.external_users_client = UsersGroupsDataSource(external_client)


            # Test connection
            if not self.test_connection_and_access():
                self.logger.error("Outlook connector connection test failed")
                return False

            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize Outlook connector: {e}")
            return False

    def test_connection_and_access(self) -> bool:
        """Test connection and access to external APIs."""
        try:
            if not self.external_outlook_client or not self.external_users_client or not self.credentials:
                return False

            # Simple test - validate credentials are present
            return (
                self.credentials.tenant_id is not None and
                self.credentials.client_id is not None and
                self.credentials.client_secret is not None
            )
        except Exception as e:
            self.logger.error(f"Connection test failed: {e}")
            return False

    async def _get_credentials(self, org_id: str) -> OutlookCredentials:
        """Load Outlook credentials from configuration."""
        try:
            config_path = f"/services/connectors/outlook/config/{org_id}"
            config = await self.config_service.get_config(config_path)

            if not config:
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

    async def _get_all_messages_delta_external(self, user_id: str, delta_link: Optional[str] = None) -> Dict:
        """Get all user messages using PROPER delta sync from external Outlook API."""
        try:
            if not self.external_outlook_client:
                raise Exception("External Outlook client not initialized")

            if delta_link:
                response: OutlookCalendarContactsResponse = await self.external_outlook_client.users_user_mail_folders_mail_folder_messages_delta(
                    user_id=user_id,
                    top=50,
                    mailFolder_id="Inbox",
                    delta_link=delta_link
                )
            else:
                response: OutlookCalendarContactsResponse = await self.external_outlook_client.users_user_mail_folders_mail_folder_messages_delta(
                    user_id=user_id,
                    mailFolder_id="Inbox",
                    top=100
                )

            if not response.success:
                self.logger.error(f"Failed to get messages delta: {response.error}")
                return {'messages': [], 'delta_link': None, 'next_link': None}

            data = response.data or {}
            messages = self._safe_get_attr(data, 'value', [])
            delta_link = (self._safe_get_attr(data, 'odata_delta_link') or
                         self._safe_get_attr(data, '@odata.deltaLink'))
            next_link = (self._safe_get_attr(data, 'odata_next_link') or
                        self._safe_get_attr(data, '@odata.nextLink'))

            return {
                'messages': messages,
                'delta_link': delta_link,
                'next_link': next_link
            }

        except Exception as e:
            self.logger.error(f"Error getting user messages delta: {e}")
            return {'messages': [], 'delta_link': None, 'next_link': None}

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

        except Exception:
            return b''

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

    def _safe_get_attr(self, obj, attr_name: str, default=None):
        """Safely get attribute from object that could be a class instance or dictionary."""
        if hasattr(obj, attr_name):
            return getattr(obj, attr_name, default)
        elif hasattr(obj, 'get'):
            return obj.get(attr_name, default)
        else:
            return default

    def get_signed_url(self, record: Record) -> Optional[str]:
        """Get signed URL for record access. Not supported for Outlook."""
        return None

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Stream record content (email or attachment)."""
        try:
            if not self.external_outlook_client:
                raise HTTPException(status_code=500, detail="External Outlook client not initialized")

            # Get the user ID from the record's mailbox group ID
            # Format: "mailbox_{user_email}" -> need to find the actual Graph user ID
            user_id = None

            # Try to get user ID from the record context
            if hasattr(record, 'external_record_group_id') and record.external_record_group_id:
                # Extract email from "mailbox_{email}" format
                if record.external_record_group_id.startswith('mailbox_'):
                    user_email = record.external_record_group_id.replace('mailbox_', '')
                    # Get all users to find the source_user_id for this email
                    all_users = await self._get_all_users_external()
                    for user in all_users:
                        if user.email == user_email:
                            user_id = user.source_user_id
                            break

            # Fallback to "me"
            if not user_id:
                user_id = "me"

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
                media_type = record.mime_type.value if record.mime_type else 'application/octet-stream'

                return StreamingResponse(generate_attachment(), media_type=media_type, headers=headers)

            else:
                raise HTTPException(status_code=400, detail="Unsupported record type for streaming")

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to stream record: {str(e)}")

    async def run_sync(self) -> None:
        """Run full Outlook sync - emails and attachments from all folders."""
        try:
            org_id = self.data_entities_processor.org_id
            self.logger.info("Starting Outlook email sync...")

            # Ensure external clients are initialized
            if not self.external_outlook_client or not self.external_users_client:
                raise Exception("External API clients not initialized. Call init() first.")

            all_users = await self._get_all_users_external()
            self.logger.info(f"Found {len(all_users)} users from Graph API")

            await self.data_entities_processor.on_new_app_users(all_users)

            async for status in self._process_users_in_batches(org_id, all_users):
                self.logger.info(status)

            self.logger.info("Outlook sync completed successfully")

        except Exception as e:
            self.logger.error(f"Error during Outlook sync: {e}")
            raise


    async def _process_users_in_batches(self, org_id: str, users: List[AppUser]) -> AsyncGenerator[str, None]:
        """Process users in batches for performance."""
        max_concurrent_users = 3
        semaphore = asyncio.Semaphore(max_concurrent_users)

        async def process_user(user: AppUser) -> str:
            async with semaphore:
                return await self._process_user_emails(org_id, user)

        tasks = [process_user(user) for user in users]

        for i, task in enumerate(asyncio.as_completed(tasks)):
            result = await task
            yield f"User {i+1}/{len(users)}: {result}"

    async def _process_user_emails(self, org_id: str, user: AppUser) -> str:
        """Process all emails for a single user."""
        try:
            user_id = user.source_user_id

            sync_point_key = generate_record_sync_point_key(
                RecordType.MAIL.value, "users", user_id
            )
            sync_point = await self.email_delta_sync_point.read_sync_point(sync_point_key)
            delta_link = sync_point.get('delta_link') if sync_point else None


            result = await self._get_all_messages_delta_external(user_id, delta_link)
            messages = result['messages']

            self.logger.info(f"Retrieved {len(messages)} messages for user {user.email}")


            if not messages:
                return f"No new emails for {user.email}"

            processed_count = 0
            batch_size = 2

            for i in range(0, len(messages), batch_size):
                batch = messages[i:i + batch_size]
                batch_updates = []

                for message in batch:
                    message_id = self._safe_get_attr(message, 'id')  # Define message_id for this message

                    # Check if message is deleted
                    additional_data = self._safe_get_attr(message, 'additional_data', {})
                    is_deleted = (additional_data.get('@removed', {}).get('reason') == 'deleted')

                    if is_deleted:
                        self.logger.info(f"Removing user access for deleted message: {message_id}")
                        async with self.data_store_provider.transaction() as tx_store:
                            db_user = await tx_store.get_user_by_email(user.email)
                            if db_user:
                                user_id = db_user.id
                                await tx_store.remove_user_access_to_record(Connectors.OUTLOOK, message_id, user_id)
                            else:
                                self.logger.error(f"Could not find database user for email {user.email}")
                        continue

                    update = await self._process_single_email(org_id, user, message)
                    if update:
                        batch_updates.append(update)

                    # Process attachments if present
                    has_attachments = self._safe_get_attr(message, 'has_attachments', False)
                    if has_attachments:
                        # Extract permissions for attachments (same as email permissions)
                        email_permissions = await self._extract_email_permissions(message, None, user)
                        attachment_updates = await self._process_email_attachments(org_id, user, message, email_permissions)
                        if attachment_updates:
                            batch_updates.extend(attachment_updates)

                if batch_updates:
                    self.logger.info(f"Saving batch of {len(batch_updates)} records for user {user.email}")
                    success = await self._save_records_batch(batch_updates)
                    if success:
                        processed_count += len(batch_updates)
                        self.logger.info(f"Successfully saved {len(batch_updates)} records")
                    else:
                        self.logger.error(f"Failed to save batch for user {user.email}")
                else:
                    self.logger.info("No batch updates to save for this batch")

            sync_point_data = {
                'delta_link': result.get('delta_link'),
                'next_link': result.get('next_link'),
                'last_sync_timestamp': int(datetime.now(timezone.utc).timestamp() * 1000)
            }

            await self.email_delta_sync_point.update_sync_point(sync_point_key, sync_point_data)

            return f"Processed {processed_count} items for {user.email}"

        except Exception as e:
            self.logger.error(f"Error processing emails for user {user.email}: {e}")
            return f"Failed to process {user.email}: {str(e)}"

    async def _process_single_email(self, org_id: str, user: AppUser, message) -> Optional[RecordUpdate]:
        """Process a single email."""
        try:
            message_id = self._safe_get_attr(message, 'id')

            existing_record = await self._get_existing_record(org_id, message_id)
            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False

            if not is_new:
                if (existing_record.record_name != self._safe_get_attr(message, 'subject', 'No Subject') or
                    existing_record.source_updated_at != self._parse_datetime(self._safe_get_attr(message, 'last_modified_date_time'))):
                    metadata_changed = True
                    is_updated = True

            record_id = existing_record.id if existing_record else str(uuid.uuid4())

            email_record = MailRecord(
                id=record_id,
                org_id=org_id,
                record_name=self._safe_get_attr(message, 'subject', 'No Subject'),
                record_type=RecordType.MAIL,
                external_record_id=message_id,
                external_revision_id=None,
                version=0 if is_new else existing_record.version + 1,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.OUTLOOK,
                source_created_at=self._parse_datetime(self._safe_get_attr(message, 'created_date_time')),
                source_updated_at=self._parse_datetime(self._safe_get_attr(message, 'last_modified_date_time')),
                weburl=self._safe_get_attr(message, 'web_link', ''),
                mime_type=MimeTypes.HTML.value,
                parent_external_record_id=None,
                external_record_group_id=f"mailbox_{user.email}",
                record_group_type=RecordGroupType.MAILBOX,
                subject=self._safe_get_attr(message, 'subject', 'No Subject'),
                from_email=self._extract_email_from_recipient(self._safe_get_attr(message, 'from_', None)),
                to_emails=[self._extract_email_from_recipient(r) for r in self._safe_get_attr(message, 'to_recipients', [])],
                cc_emails=[self._extract_email_from_recipient(r) for r in self._safe_get_attr(message, 'cc_recipients', [])],
                bcc_emails=[self._extract_email_from_recipient(r) for r in self._safe_get_attr(message, 'bcc_recipients', [])],
                thread_id=self._safe_get_attr(message, 'conversation_id', ''),
                is_parent=False,
                internal_date=self._format_datetime_string(self._safe_get_attr(message, 'received_date_time')),
                date=self._format_datetime_string(self._safe_get_attr(message, 'sent_date_time')),
                message_id_header=self._safe_get_attr(message, 'internet_message_id', ''),
                history_id='',
                label_ids=self._safe_get_attr(message, 'categories', []),
            )

            permissions = await self._extract_email_permissions(message, email_record.id, user)

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

    async def _process_email_attachments(self, org_id: str, user: AppUser, message: Dict, email_permissions: List[Permission]) -> List[RecordUpdate]:
        """Process email attachments."""
        attachment_updates = []

        try:
            user_id = user.source_user_id

            message_id = self._safe_get_attr(message, 'id')

            attachments = await self._get_message_attachments_external(user_id, message_id)

            for i, attachment in enumerate(attachments):

                attachment_id = self._safe_get_attr(attachment, 'id')

                existing_record = await self._get_existing_record(org_id, attachment_id)
                is_new = existing_record is None

                content_type = self._safe_get_attr(attachment, 'content_type', 'application/octet-stream')
                mime_type = self._get_mime_type_enum(content_type)

                file_name = self._safe_get_attr(attachment, 'name', 'Unnamed Attachment')
                extension = None
                if '.' in file_name:
                    extension = file_name.split('.')[-1].lower()

                attachment_record_id = existing_record.id if existing_record else str(uuid.uuid4())

                attachment_record = FileRecord(
                    id=attachment_record_id,
                    org_id=org_id,
                    record_name=file_name,
                    record_type=RecordType.FILE,
                    external_record_id=attachment_id,
                    external_revision_id=None,
                    version=0 if is_new else existing_record.version + 1,
                    origin=OriginTypes.CONNECTOR,
                    connector_name=Connectors.OUTLOOK,
                    source_created_at=self._parse_datetime(self._safe_get_attr(attachment, 'last_modified_date_time')),
                    source_updated_at=self._parse_datetime(self._safe_get_attr(attachment, 'last_modified_date_time')),
                    mime_type=mime_type,
                    parent_external_record_id=message_id,
                    parent_record_type=RecordType.MAIL,
                    external_record_group_id=f"mailbox_{user.email}",
                    record_group_type=RecordGroupType.MAILBOX,
                    weburl="",
                    is_file=True,
                    size_in_bytes=self._safe_get_attr(attachment, 'size', 0),
                    extension=extension,
                )

                attachment_updates.append(RecordUpdate(
                    record=attachment_record,
                    is_new=is_new,
                    is_updated=False,
                    is_deleted=False,
                    metadata_changed=False,
                    content_changed=False,
                    permissions_changed=bool(email_permissions),
                    new_permissions=email_permissions,  # Inherit permissions from parent email
                    external_record_id=attachment_id,
                ))

            return attachment_updates

        except Exception as e:
            self.logger.error(f"Error processing attachments for email {self._safe_get_attr(message, 'id', 'unknown')}: {e}")
            return []


    async def _extract_email_permissions(self, message: Dict, record_id: Optional[str], inbox_owner: AppUser) -> List[Permission]:
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
            inbox_owner_email = inbox_owner.email.lower()

            for recipient in all_recipients:
                try:
                    email_address = self._extract_email_from_recipient(recipient)
                    if email_address and email_address not in processed_emails:
                        processed_emails.add(email_address)

                        if email_address.lower() == inbox_owner_email:
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

    async def _get_existing_record(self, org_id: str, external_record_id: str) -> Optional[Record]:
        """Get existing record from data store."""
        # TODO: Implement when data store supports efficient record lookup by external ID
        return None

    async def _save_records_batch(self, updates: List[RecordUpdate]) -> bool:
        """Save a batch of record updates."""
        try:
            permissions_to_save = []

            for update in updates:
                if update.new_permissions:
                    permissions_to_save.extend(update.new_permissions)

            records_with_permissions = []
            for update in updates:
                if update.record:
                    permissions = update.new_permissions or []
                    records_with_permissions.append((update.record, permissions))

            await self.data_entities_processor.on_new_records(records_with_permissions)
            success = True


            return success

        except Exception as e:
            self.logger.error(f"Error saving records batch: {e}")
            return False

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


    async def handle_webhook_notification(self, org_id: str, notification: Dict) -> bool:
        """Handle webhook notifications from Microsoft Graph."""
        try:
            return True
        except Exception as e:
            self.logger.error(f"Error handling webhook notification: {e}")
            return False

    def cleanup(self) -> None:
        """Clean up resources used by the connector."""
        try:
            self.external_outlook_client = None
            self.external_users_client = None
            self.credentials = None
        except Exception as e:
            self.logger.error(f"Error during Outlook connector cleanup: {e}")

    async def run_incremental_sync(self) -> None:
        """Run incremental synchronization for Outlook emails."""
        # Delegate to full sync - incremental is handled by delta links
        await self.run_sync()

    @classmethod
    async def create_connector(cls, logger: Logger, data_store_provider: DataStoreProvider, config_service: ConfigurationService) -> 'OutlookConnector':
        """Factory method to create and initialize OutlookConnector."""
        data_entities_processor = DataSourceEntitiesProcessor(logger, data_store_provider, config_service)
        await data_entities_processor.initialize()

        return OutlookConnector(logger, data_entities_processor, data_store_provider, config_service)
