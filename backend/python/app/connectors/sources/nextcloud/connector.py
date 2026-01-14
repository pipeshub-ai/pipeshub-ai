import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from logging import Logger
from typing import AsyncGenerator, Dict, List, NoReturn, Optional, Tuple
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from aiolimiter import AsyncLimiter
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

# Base connector and service imports
from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
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
)
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
)
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
)
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperator,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate

# App-specific Nextcloud client imports
from app.connectors.sources.nextcloud.common.apps import NextcloudApp

# Model imports
from app.models.entities import (
    AppUser,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.nextcloud.nextcloud import (
    NextcloudClient,
)
from app.sources.external.nextcloud.nextcloud import NextcloudDataSource
from app.utils.time_conversion import get_epoch_timestamp_in_ms

NEXTCLOUD_PERM_MASK_ALL = 31
HTTP_STATUS_OK = 200
HTTP_STATUS_MULTIPLE_CHOICES = 300
# Helper functions
def get_parent_path_from_path(path: str) -> Optional[str]:
    """Extracts the parent path from a file/folder path."""
    if not path or path == "/" or "/" not in path.lstrip("/"):
        return None
    parent_path = "/".join(path.strip("/").split("/")[:-1])
    return f"/{parent_path}" if parent_path else "/"


def get_path_depth(path: str) -> int:
    """Calculate the depth of a path (number of directory levels)."""
    if not path or path == "/":
        return 0
    return len([p for p in path.strip("/").split("/") if p])


def get_file_extension(filename: str) -> Optional[str]:
    """Extracts the extension from a filename."""
    if "." in filename:
        parts = filename.split(".")
        if len(parts) > 1:
            return parts[-1].lower()
    return None


def get_mimetype_enum_for_nextcloud(mime_type: str, is_collection: bool) -> MimeTypes:
    """
    Determines the correct MimeTypes enum member for a Nextcloud entry.
    Args:
        mime_type: The MIME type from WebDAV getcontenttype
        is_collection: Whether this is a folder (from resourcetype)
    Returns:
        The corresponding MimeTypes enum member.
    """
    if is_collection:
        return MimeTypes.FOLDER

    if mime_type:
        try:
            return MimeTypes(mime_type)
        except ValueError:
            return MimeTypes.BIN

    return MimeTypes.BIN


def parse_webdav_propfind_response(xml_response: bytes) -> List[Dict]:
    """
    Parse a WebDAV PROPFIND XML response into a list of file/folder dictionaries.
    Args:
        xml_response: The XML bytes returned from PROPFIND
    Returns:
        List of dictionaries containing file/folder properties
    """
    entries = []

    try:
        root = ET.fromstring(xml_response)

        # Define namespaces
        namespaces = {
            'd': 'DAV:',
            'oc': 'http://owncloud.org/ns',
            'nc': 'http://nextcloud.org/ns'
        }

        # Find all response elements
        for response in root.findall('d:response', namespaces):
            entry = {}

            # Get href (path)
            href = response.find('d:href', namespaces)
            if href is not None and href.text:
                # Decode URL-encoded path
                entry['path'] = unquote(href.text)

            # Get properties
            propstat = response.find('d:propstat', namespaces)
            if propstat is not None:
                prop = propstat.find('d:prop', namespaces)
                if prop is not None:
                    # Extract all relevant properties
                    last_modified_elem = prop.find('d:getlastmodified', namespaces)
                    if last_modified_elem is not None:
                        entry['last_modified'] = last_modified_elem.text

                    etag_elem = prop.find('d:getetag', namespaces)
                    if etag_elem is not None:
                        entry['etag'] = etag_elem.text

                    content_type_elem = prop.find('d:getcontenttype', namespaces)
                    if content_type_elem is not None:
                        entry['content_type'] = content_type_elem.text

                    file_id_elem = prop.find('oc:fileid', namespaces)
                    if file_id_elem is not None:
                        entry['file_id'] = file_id_elem.text

                    permissions_elem = prop.find('oc:permissions', namespaces)
                    if permissions_elem is not None:
                        entry['permissions'] = permissions_elem.text

                    size_elem = prop.find('oc:size', namespaces)
                    if size_elem is not None:
                        entry['size'] = int(size_elem.text) if size_elem.text else 0

                    content_length_elem = prop.find('d:getcontentlength', namespaces)
                    if content_length_elem is not None:
                        entry['content_length'] = int(content_length_elem.text) if content_length_elem.text else 0

                    display_name_elem = prop.find('d:displayname', namespaces)
                    if display_name_elem is not None:
                        entry['display_name'] = display_name_elem.text

                    # Check if it's a collection (folder)
                    resourcetype = prop.find('d:resourcetype', namespaces)
                    entry['is_collection'] = resourcetype is not None and \
                                            resourcetype.find('d:collection', namespaces) is not None

            # Only add entries with a file_id
            if entry.get('file_id'):
                entries.append(entry)

    except ET.ParseError as e:
        error_context = xml_response[:500].decode('utf-8', errors='replace') if xml_response else 'empty'
        logger = logging.getLogger(__name__)
        logger.error(
            f"Failed to parse WebDAV XML response: {e}. "
            f"Response preview: {error_context}...",
            exc_info=True
        )
        # Return empty list to allow sync to continue for other files
        return []
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Unexpected error parsing WebDAV response: {e}", exc_info=True)
        return []

    return entries


def parse_share_response(response_body: bytes) -> List[Dict]:
    """
    Parse an OCS share response JSON into a list of share dictionaries.
    Args:
        response_body: The JSON bytes returned from OCS share API
    Returns:
        List of dictionaries containing share information
    """
    shares = []

    try:
        data = json.loads(response_body)

        # OCS API response structure: {"ocs": {"meta": {...}, "data": [...]}}
        ocs_data = data.get('ocs', {}).get('data', [])

        # Handle case where data might be a list or single dict
        if isinstance(ocs_data, dict):
            # Single share response
            share_list = [ocs_data] if ocs_data else []
        elif isinstance(ocs_data, list):
            share_list = ocs_data
        else:
            return shares

        for share_item in share_list:
            if not isinstance(share_item, dict):
                continue

            share = {}

            # Extract share properties with safe type conversion
            if 'share_type' in share_item:
                try:
                    share['share_type'] = int(share_item['share_type'])
                except (ValueError, TypeError):
                    logger = logging.getLogger(__name__)
                    logger.debug(
                        f"Invalid share_type value: {share_item.get('share_type')}, "
                        f"skipping this field"
                    )

            if 'share_with' in share_item:
                share_with_value = share_item['share_with']
                if share_with_value and isinstance(share_with_value, str):
                    share['share_with'] = share_with_value.strip()

            if 'share_with_displayname' in share_item:
                display_name = share_item['share_with_displayname']
                if display_name and isinstance(display_name, str):
                    share['share_with_displayname'] = display_name.strip()
                else:
                    if 'share_with' in share:
                        share['share_with_displayname'] = share['share_with']

            if 'permissions' in share_item:
                try:
                    perm_value = int(share_item['permissions'])
                    if 0 <= perm_value <= NEXTCLOUD_PERM_MASK_ALL:
                        share['permissions'] = perm_value
                    else:
                        share['permissions'] = 1
                except (ValueError, TypeError):
                    share['permissions'] = 1

            if 'uid_owner' in share_item:
                uid_owner = share_item['uid_owner']
                if uid_owner and isinstance(uid_owner, str):
                    share['uid_owner'] = uid_owner.strip()

            if share and ('share_type' in share or 'share_with' in share):
                shares.append(share)

    except json.JSONDecodeError as e:
        error_context = response_body[:500].decode('utf-8', errors='replace') if response_body else 'empty'
        logger = logging.getLogger(__name__)
        logger.error(
            f"Failed to parse OCS share response as JSON: {e}. "
            f"Response preview: {error_context}...",
            exc_info=True
        )
        return []
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Unexpected error parsing share response: {e}", exc_info=True)
        return []

    return shares


def nextcloud_permissions_to_permission_type(permissions: int) -> PermissionType:
    """
    Convert Nextcloud permission integer to PermissionType.
    Nextcloud permissions are bitmasks:
    - 1: READ
    - 2: UPDATE
    - 4: CREATE
    - 8: DELETE
    - 16: SHARE
    - 31: ALL (typically OWNER/ADMIN)
    """
    if permissions == NEXTCLOUD_PERM_MASK_ALL:
        return PermissionType.OWNER
    elif permissions & 8 or permissions & 2:
        return PermissionType.WRITE
    elif permissions & 1:
        return PermissionType.READ
    else:
        return PermissionType.READ


def extract_response_body(response) -> Optional[bytes]:
    """Safely extract body from response object."""
    if hasattr(response, 'bytes') and callable(response.bytes):
        try:
            result = response.bytes()
            if result is not None:
                return result
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.debug(f"Failed to extract bytes from response: {e}")

    if hasattr(response, 'text') and callable(response.text):
        try:
            text_result = response.text()
            if text_result is not None:
                if isinstance(text_result, str):
                    return text_result.encode('utf-8')
                return text_result
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.debug(f"Failed to extract text from response: {e}")

    if hasattr(response, 'response') and hasattr(response.response, 'content'):
        try:
            content = response.response.content
            if content is not None:
                return content
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.debug(f"Failed to extract content from response: {e}")

    return None


def is_response_successful(response) -> bool:
    """Check if response indicates success."""
    if hasattr(response, 'success'):
        return response.success

    if hasattr(response, 'status'):
        return HTTP_STATUS_OK <= response.status < HTTP_STATUS_MULTIPLE_CHOICES

    if hasattr(response, 'status_code'):
        return HTTP_STATUS_OK <= response.status_code < HTTP_STATUS_MULTIPLE_CHOICES

    return False


def get_response_error(response) -> str:
    """Extract error message from response."""
    if hasattr(response, 'error') and response.error:
        return str(response.error)

    if hasattr(response, 'status'):
        return f"HTTP {response.status}"

    if hasattr(response, 'status_code'):
        return f"HTTP {response.status_code}"

    return "Unknown error"


@ConnectorBuilder("Nextcloud")\
    .in_group("Cloud Storage")\
    .with_description("Sync files and folders from your personal Nextcloud account")\
    .with_categories(["Storage", "Collaboration"])\
    .with_scopes([ConnectorScope.PERSONAL.value])\
    .with_auth([
        AuthBuilder.type(AuthType.BASIC_AUTH).fields([
            # 1. Base URL is always required
            CommonFields.base_url("Nextcloud"),
            # 2. For Nextcloud App Tokens, you usually need a Username AND the Token (as password)
            # We make username required for stability
            AuthField(
                name="username",
                display_name="Username",
                placeholder="e.g. admin or myuser",
                description="Your Nextcloud username",
                required=True,
                min_length=1
            ),
            # 3. The App Password / Token
            # We use a password field so it is masked in the UI
            AuthField(
                name="password",
                display_name="App Password / Token",
                placeholder="e.g. xxxx-xxxx-xxxx-xxxx",
                description="Generated App Password from Nextcloud Security Settings",
                field_type="PASSWORD",
                required=True,
                min_length=1,
                is_secret=True
            )
        ])
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/nextcloud.svg")
        .with_realtime_support(False)
        .add_documentation_link(DocumentationLink(
            "Nextcloud API Documentation",
            "https://docs.nextcloud.com/server/latest/developer_manual/",
            "api"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/nextcloud',
            'pipeshub'
        ))
        .with_scheduled_config(True, 60)
        # Optional: Keep batch size
        .add_filter_field(CommonFields.file_extension_filter())
        .add_sync_custom_field(CommonFields.batch_size_field())
        .with_sync_support(True)
        .with_agent_support(False)
    )\
    .build_decorator()
class NextcloudConnector(BaseConnector):
    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
    ) -> None:
        super().__init__(
            NextcloudApp(connector_id=connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id=connector_id
        )

        self.connector_name = Connectors.NEXTCLOUD
        self.connector_id = connector_id

        # Initialize sync point for records only (personal connector)
        self.activity_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=self.data_entities_processor.org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=self.data_store_provider
        )

        # Store current user info for personal account
        self.current_user_id: Optional[str] = None
        self.current_user_email: Optional[str] = None

        self.data_source: Optional[NextcloudDataSource] = None
        self.batch_size = 100
        self.max_concurrent_batches = 5
        self.rate_limiter = AsyncLimiter(50, 1)
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

        # Cache for path-to-external-id mapping during sync
        self._path_to_external_id_cache: Dict[str, str] = {}

    async def init(self) -> bool:
        """Initialize the Nextcloud client for personal account."""
        try:
            # Get current user info from config
            config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config"
            )
            
            if not config:
                self.logger.error("Nextcloud connector configuration not found")
                return False
            
            # Debug: Log the config structure (without sensitive data)
            self.logger.debug(f"Config keys: {list(config.keys())}")
            
            auth_config = config.get("auth", {}) or {}
            credentials_config = config.get("credentials", {}) or {}
            
            self.logger.debug(f"Auth config keys: {list(auth_config.keys()) if auth_config else 'None'}")
            self.logger.debug(f"Credentials config keys: {list(credentials_config.keys()) if credentials_config else 'None'}")
            
            if not auth_config:
                self.logger.error("Auth configuration not found")
                return False
            
            # Extract credentials - check both locations
            base_url = (
                auth_config.get("baseUrl") or 
                credentials_config.get("baseUrl") or 
                config.get("baseUrl")
            )
            
            if not base_url:
                self.logger.error(f"Nextcloud 'baseUrl' is required in configuration. Checked auth_config, credentials_config, and root config")
                return False
            
            username = auth_config.get("username")
            password = auth_config.get("password")
            
            if not username or not password:
                self.logger.error("Username and Password are required for Nextcloud")
                return False
            
            # Build client directly
            from app.sources.client.nextcloud.nextcloud import NextcloudRESTClientViaUsernamePassword
            client = NextcloudRESTClientViaUsernamePassword(base_url, username, password)
            nextcloud_client = NextcloudClient(client)
            
            # Initialize data source
            self.data_source = NextcloudDataSource(nextcloud_client)
            
            # Store current user info
            self.current_user_id = username
            
            # Try to get user email from Nextcloud
            try:
                response = await self.data_source.get_user_details(self.current_user_id)
                if is_response_successful(response):
                    body = extract_response_body(response)
                    if body:
                        data = json.loads(body)
                        user_data = data.get('ocs', {}).get('data', {})
                        self.current_user_email = user_data.get('email') or f"{self.current_user_id}@nextcloud.local"
                else:
                    self.current_user_email = f"{self.current_user_id}@nextcloud.local"
            except Exception as e:
                self.logger.warning(f"Could not fetch user email: {e}")
                self.current_user_email = f"{self.current_user_id}@nextcloud.local"
            
            self.logger.info(f"Nextcloud client initialized for user: {self.current_user_id}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize Nextcloud client: {e}", exc_info=True)
            return False


    def _sort_entries_by_hierarchy(self, entries: List[Dict]) -> List[Dict]:
        """
        Sort entries so folders are processed before their contents.
        This ensures parent records exist before children reference them.
        Args:
            entries: List of file/folder entries from WebDAV
        Returns:
            Sorted list with folders first, then files, ordered by depth
        """
        # Separate folders and files
        folders = []
        files = []

        for entry in entries:
            if entry.get('is_collection'):
                folders.append(entry)
            else:
                files.append(entry)

        # Sort folders by depth (shallowest first)
        folders.sort(key=lambda e: get_path_depth(e.get('path', '')))

        # Sort files by depth (shallowest first)
        files.sort(key=lambda e: get_path_depth(e.get('path', '')))

        # Return folders first, then files
        return folders + files

    async def _build_path_to_external_id_map(
        self,
        entries: List[Dict]
    ) -> Dict[str, str]:
        """
        Build an in-memory map of paths to external IDs for quick parent lookups.
        This avoids database queries during processing.
        Args:
            entries: List of entries to map
        Returns:
            Dictionary mapping path -> external_record_id
        """
        path_map = {}

        for entry in entries:
            file_id = entry.get('file_id')
            path = entry.get('path')

            if file_id and path:
                path_map[path] = file_id

        return path_map

    async def _process_nextcloud_entry(
        self,
        entry: Dict,
        user_id: str,
        user_email: str,
        record_group_id: str,
        user_root_path: Optional[str],
        path_to_external_id: Dict[str, str]
    ) -> Optional[RecordUpdate]:
        """
        Process a single Nextcloud entry and detect changes.
        """
        try:
            # Extract basic properties
            file_id = entry.get('file_id')
            if not file_id:
                return None

            path = entry.get('path', '')
            # Normalize path for cache consistency
            clean_path = path.rstrip('/')

            display_name = entry.get('display_name', path.split('/')[-1] if path else 'Unknown')
            is_collection = entry.get('is_collection', False)
            etag = entry.get('etag', '').strip('"')
            size = entry.get('size', 0)
            content_type = entry.get('content_type')
            last_modified_str = entry.get('last_modified')

            # Apply file extension filter for files
            if not is_collection and not self._should_include_file(entry):
                self.logger.debug(f"File {display_name} filtered out by extension filter")
                return None

            # Parse last modified timestamp
            timestamp_ms = get_epoch_timestamp_in_ms()
            if last_modified_str:
                try:
                    dt = datetime.strptime(last_modified_str, "%a, %d %b %Y %H:%M:%S %Z")
                    timestamp_ms = int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
                except ValueError as e:
                    self.logger.debug(f"Failed to parse last_modified timestamp '{last_modified_str}': {e}, using current timestamp")

            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    external_id=file_id,
                    connector_id=self.connector_id
                )

            # Detect changes
            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False
            permissions_changed = False

            if existing_record:
                if existing_record.record_name != display_name:
                    metadata_changed = True
                    is_updated = True
                if existing_record.external_revision_id != etag:
                    content_changed = True
                    is_updated = True

            record_type = RecordType.FILE

            # Parent Resolution
            parent_external_record_id = None
            parent_path = get_parent_path_from_path(path)

            if parent_path and parent_path != '/':
                clean_parent_path = parent_path.rstrip('/')

                # A. Check if parent is the User Root (Stop looking!)
                if user_root_path and clean_parent_path == user_root_path:
                    parent_external_record_id = None

                # B. Check In-Memory Cache (Parent processed in this batch?)
                elif clean_parent_path in path_to_external_id:
                    parent_external_record_id = path_to_external_id[clean_parent_path]

                # C. Fallback to Database Lookup
                else:
                    try:
                        async with self.data_store_provider.transaction() as tx_store:
                            # Use original parent_path for DB lookup to match stored format
                            parent_record = await tx_store.get_record_by_path(
                                connector_id=self.connector_id,
                                path=parent_path
                            )

                            if parent_record:
                                parent_external_record_id = parent_record.external_record_id
                                # Cache it for future lookups
                                path_to_external_id[clean_parent_path] = parent_external_record_id
                            else:
                                # Only log debug if we really expected a parent
                                self.logger.debug(
                                    f"Parent path {parent_path} not found in DB or Cache for {display_name}."
                                )
                    except Exception as parent_ex:
                        self.logger.debug(f"Parent lookup failed: {parent_ex}")

            # Create FileRecord
            file_record = FileRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=display_name,
                record_type=record_type,
                record_group_type=RecordGroupType.DRIVE.value,
                external_record_group_id=record_group_id,
                external_record_id=file_id,
                external_revision_id=etag,
                version=0 if is_new else existing_record.version + 1,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name.value,
                connector_id=self.connector_id,
                created_at=timestamp_ms,
                updated_at=timestamp_ms,
                source_created_at=timestamp_ms,
                source_updated_at=timestamp_ms,
                weburl="",
                signed_url=None,
                parent_external_record_id=parent_external_record_id,
                size_in_bytes=size,
                is_file=not is_collection,
                extension=get_file_extension(display_name) if not is_collection else "",
                path=path,
                mime_type=get_mimetype_enum_for_nextcloud(content_type, is_collection),
                etag=etag,
                sha256_hash=None,
            )

            if file_id:
                path_to_external_id[clean_path] = file_id

            # Fetch permissions
            new_permissions = []
            try:
                new_permissions = await self._convert_nextcloud_permissions_to_permissions(
                    path=path,
                    user_id=user_id
                )

                if not new_permissions:
                    new_permissions = [
                        Permission(
                            external_id=user_id,
                            email=user_email,
                            type=PermissionType.OWNER,
                            entity_type=EntityType.USER
                        )
                    ]
                else:
                    user_already_has_permission = any(
                        perm.email == user_email for perm in new_permissions
                    )
                    if not user_already_has_permission:
                        new_permissions.append(
                            Permission(
                                external_id=user_id,
                                email=user_email,
                                type=PermissionType.OWNER,
                                entity_type=EntityType.USER
                            )
                        )

            except Exception as perm_ex:
                self.logger.debug(f"Using default permissions for {path}: {perm_ex}")
                new_permissions = [
                    Permission(
                        external_id=user_id,
                        email=user_email,
                        type=PermissionType.OWNER,
                        entity_type=EntityType.USER
                    )
                ]

            # Compare permissions
            old_permissions = []
            if existing_record:
                try:
                    async with self.data_store_provider.transaction() as tx_store:
                        old_permissions = await tx_store.get_permissions_for_record(
                            record_id=existing_record.id
                        )

                    permissions_changed = self._compare_permissions(
                        old_permissions,
                        new_permissions
                    )

                    if permissions_changed:
                        is_updated = True

                except Exception:
                    permissions_changed = True
                    is_updated = True

            return RecordUpdate(
                record=file_record,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=False,
                metadata_changed=metadata_changed,
                content_changed=content_changed,
                permissions_changed=permissions_changed,
                old_permissions=old_permissions,
                new_permissions=new_permissions,
                external_record_id=file_id
            )

        except Exception as ex:
            self.logger.error(
                f"Error processing entry {entry.get('file_id', entry.get('path'))}: {ex}",
                exc_info=True
            )
            return None

    async def _process_nextcloud_items_generator(
        self,
        entries: List[Dict],
        user_id: str,
        user_email: str,
        record_group_id: str,
        user_root_path: Optional[str],
        path_to_external_id: Dict[str, str]
    ) -> AsyncGenerator[Tuple[Optional[FileRecord], List[Permission], RecordUpdate], None]:
        """Process Nextcloud entries and yield records with their permissions."""
        for entry in entries:
            try:
                # Updated call to use the correct argument names
                record_update = await self._process_nextcloud_entry(
                    entry,
                    user_id,
                    user_email,
                    record_group_id,
                    user_root_path,
                    path_to_external_id
                )
                if record_update:
                    yield (
                        record_update.record,
                        record_update.new_permissions or [],
                        record_update
                    )
                await asyncio.sleep(0)
            except Exception as e:
                self.logger.error(f"Error processing item in generator: {e}", exc_info=True)
                continue

    def _compare_permissions(
        self,
        old_permissions: List[Permission],
        new_permissions: List[Permission]
    ) -> bool:
        """Compare two permission lists to detect changes."""
        if len(old_permissions) != len(new_permissions):
            return True

        old_set = {
            (p.email, p.type.value if hasattr(p.type, 'value') else p.type,
             p.entity_type.value if hasattr(p.entity_type, 'value') else p.entity_type)
            for p in old_permissions
        }
        new_set = {
            (p.email, p.type.value if hasattr(p.type, 'value') else p.type,
             p.entity_type.value if hasattr(p.entity_type, 'value') else p.entity_type)
            for p in new_permissions
        }

        return old_set != new_set

    async def _convert_nextcloud_permissions_to_permissions(
        self,
        path: str,
        user_id: str
    ) -> List[Permission]:
        """Convert Nextcloud share permissions to Permission model."""
        permissions = []

        try:
            relative_path = path
            if '/files/' in path:
                parts = path.split('/files/')
                if len(parts) > 1:
                    user_and_path = parts[1]
                    path_parts = user_and_path.split('/', 1)
                    if len(path_parts) > 1:
                        relative_path = '/' + path_parts[1].rstrip('/')
                    else:
                        relative_path = '/'

            if relative_path == '/' or not relative_path:
                return []

            response = await self.data_source.get_shares(
                path=relative_path,
                reshares=True,
                subfiles=False
            )

            if not is_response_successful(response):
                return []

            body = extract_response_body(response)
            if not body:
                return []

            shares = parse_share_response(body)

            for share in shares:
                share_type = share.get('share_type')
                share_with = share.get('share_with')
                permission_int = share.get('permissions', 1)

                if share_type == 0:  # User share
                    permissions.append(
                        Permission(
                            external_id=share_with,
                            email=None,
                            type=nextcloud_permissions_to_permission_type(permission_int),
                            entity_type=EntityType.USER
                        )
                    )
                elif share_type == 1:  # Group share
                    permissions.append(
                        Permission(
                            external_id=share_with,
                            email=None,
                            type=nextcloud_permissions_to_permission_type(permission_int),
                            entity_type=EntityType.GROUP
                        )
                    )

        except Exception as e:
            self.logger.debug(f"Error converting permissions for {path}: {e}")

        return permissions

    async def _handle_record_updates(self, record_update: RecordUpdate) -> None:
        """Handle different types of record updates."""
        try:
            if record_update.is_deleted:
                await self.data_entities_processor.on_record_deleted(
                    record_id=record_update.external_record_id
                )
            elif record_update.is_new:
                self.logger.debug(f"New record: {record_update.record.record_name}")
            elif record_update.is_updated:
                if record_update.metadata_changed:
                    await self.data_entities_processor.on_record_metadata_update(
                        record_update.record
                    )
                if record_update.permissions_changed:
                    await self.data_entities_processor.on_updated_record_permissions(
                        record_update.record,
                        record_update.new_permissions
                    )
                if record_update.content_changed:
                    await self.data_entities_processor.on_record_content_update(
                        record_update.record
                    )
        except Exception as e:
            self.logger.error(f"Error handling record updates: {e}", exc_info=True)

    async def _sync_user_files(
        self,
        user_id: str,
        user_email: str,
        record_group_id: str
    ) -> None:
        """
        Synchronize all files for a specific user using WebDAV PROPFIND.
        """
        try:
            self.logger.info(f"Syncing files for user: {user_email}")

            async with self.rate_limiter:
                response = await self.data_source.list_directory(
                    user_id=user_id,
                    path="",
                    depth="infinity"
                )

            if not is_response_successful(response):
                self.logger.error(
                    f"Failed to list directory for {user_email}: {get_response_error(response)}"
                )
                return

            body = extract_response_body(response)
            if not body:
                self.logger.error(f"Empty response for {user_email}")
                return

            # Parse WebDAV response
            entries = parse_webdav_propfind_response(body)

            # 1. Capture the Root Path
            user_root_path = None
            if entries:
                # e.g. /remote.php/dav/files/NC_Admin
                user_root_path = entries[0].get('path', '').rstrip('/')

            # Skip the root directory entry
            entries = entries[1:] if len(entries) > 1 else []

            # 2. Initialize the Cache with CORRECT variable name
            path_to_external_id = {}

            self.logger.info(f"Found {len(entries)} entries for {user_email}")

            if not entries:
                self.logger.info(f"No files to sync for {user_email}")
                return

            # Sort entries by hierarchy (folders first, by depth)
            sorted_entries = self._sort_entries_by_hierarchy(entries)

            # Build path-to-external-id map for fast parent lookups
            # This pre-populates the cache with all known paths
            path_to_external_id = await self._build_path_to_external_id_map(sorted_entries)

            # Process entries in batches
            batch_records = []
            batch_count = 0
            updated_count = 0
            new_count = 0

            # Pass correct variable name to generator
            async for file_record, permissions, record_update in self._process_nextcloud_items_generator(
                sorted_entries,           # Use sorted entries
                user_id,
                user_email,
                record_group_id,
                user_root_path,           # Pass user root path
                path_to_external_id
            ):
                # Handle updates separately from new records
                if record_update.is_updated and not record_update.is_new:
                    await self._handle_record_updates(record_update)
                    updated_count += 1
                    continue

                # Collect new records for batch processing
                if file_record and record_update.is_new:
                    batch_records.append((file_record, permissions))
                    batch_count += 1
                    new_count += 1

                    if batch_count >= self.batch_size:
                        await self.data_entities_processor.on_new_records(batch_records)
                        batch_records = []
                        batch_count = 0
                        await asyncio.sleep(0.1)

            # Process remaining records
            if batch_records:
                await self.data_entities_processor.on_new_records(batch_records)

            self.logger.info(
                f"Sync complete for {user_email}: {new_count} new, {updated_count} updated"
            )

        except Exception as e:
            self.logger.error(f"Error syncing files for {user_email}: {e}", exc_info=True)

    async def run_sync(self) -> None:
        """
        Runs a full synchronization from the Nextcloud personal account.
        """
        try:
            self.logger.info("Starting Nextcloud personal account sync.")

            # Load filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "nextcloud", self.connector_id, self.logger
            )

            # Clear cache at start of sync
            self._path_to_external_id_cache.clear()

            if self.data_source is None:
                self.logger.warning("Data source not initialized, attempting to initialize...")
                init_success = await self.init()
                if not init_success or self.data_source is None:
                    self.logger.error(
                        "Cannot run sync: Failed to initialize Nextcloud data source. "
                        "Check your configuration (baseUrl, username, password)."
                    )
                    return

            if not self.current_user_id or not self.current_user_email:
                self.logger.error("Current user info not available")
                return

            # Create a single app user for the current user
            app_user = AppUser(
                app_name=self.connector_name,
                connector_id=self.connector_id,
                source_user_id=self.current_user_id,
                full_name=self.current_user_id,
                email=self.current_user_email,
                is_active=True,
                title=None,
            )
            
            await self.data_entities_processor.on_new_app_users([app_user])

            # Create a record group for the personal drive
            record_group = RecordGroup(
                name=f"{self.current_user_id}'s Files",
                org_id=self.data_entities_processor.org_id,
                description="Personal Nextcloud Folder",
                external_group_id=self.current_user_id,
                connector_name=self.connector_name.value,
                connector_id=self.connector_id,
                group_type=RecordGroupType.DRIVE,
            )

            user_permission = Permission(
                email=self.current_user_email,
                type=PermissionType.OWNER,
                entity_type=EntityType.USER
            )

            await self.data_entities_processor.on_new_record_groups([(record_group, [user_permission])])

            # Sync files for the current user only
            self.logger.info(f"Syncing files for user: {self.current_user_email}")
            await self._sync_user_files(
                self.current_user_id,
                self.current_user_email,
                self.current_user_id
            )

            self.logger.info("Nextcloud personal account sync completed successfully.")

        except Exception as ex:
            self.logger.error(f"Error in Nextcloud connector run: {ex}", exc_info=True)
            raise
        finally:
            # Clear cache after sync
            self._path_to_external_id_cache.clear()

    async def run_incremental_sync(self) -> None:
        """Runs an incremental sync (placeholder for future implementation)."""
        self.logger.info(
            "Incremental sync not yet implemented for Nextcloud. "
            "Running full sync instead."
        )
        await self.run_sync()

    async def get_signed_url(self, record: Record) -> Optional[str]:
        """
        Generate a signed/temporary URL for a file.
        Note: Nextcloud personal accounts use authenticated downloads,
        so this returns None. Use stream_record for downloading files.
        """
        # Nextcloud doesn't provide public signed URLs for personal accounts
        # Downloads are handled through authenticated WebDAV requests
        return None

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Stream a file's content for download using authenticated request."""
        if not self.data_source:
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail="Data source not initialized"
            )
            
        # Get file record to get path
        async with self.data_store_provider.transaction() as tx_store:
            file_record = await tx_store.get_file_record_by_id(record.id)
        
        if not file_record or not file_record.path:
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail="File not found or access denied"
            )
            
        # Check if it's a folder
        if file_record.mime_type == MimeTypes.FOLDER:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Cannot download folders"
            )
        
        # Extract relative path from full WebDAV path
        path = file_record.path
        relative_path = path
        if '/files/' in path:
            parts = path.split('/files/')
            if len(parts) > 1:
                user_and_path = parts[1]
                path_parts = user_and_path.split('/', 1)
                if len(path_parts) > 1:
                    relative_path = path_parts[1]
                else:
                    relative_path = ''
        else:
            relative_path = path.lstrip('/')
        
        # Download file using authenticated WebDAV client
        try:
            response = await self.data_source.download_file(
                user_id=self.current_user_id,
                path=relative_path
            )
            
            if not is_response_successful(response):
                raise HTTPException(
                    status_code=HttpStatusCode.NOT_FOUND.value,
                    detail=f"Failed to download file: {get_response_error(response)}"
                )
            
            # Get file content from response
            file_content = extract_response_body(response)
            if not file_content:
                raise HTTPException(
                    status_code=HttpStatusCode.NOT_FOUND.value,
                    detail="Empty file content"
                )
            
            # Create async generator for streaming
            async def generate():
                yield file_content
            
            return StreamingResponse(
                generate(),
                media_type=record.mime_type if record.mime_type else "application/octet-stream",
                headers={
                    "Content-Disposition": f"attachment; filename={record.record_name}"
                }
            )
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Error streaming record {record.id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail=f"Error streaming file: {str(e)}"
            )

    async def test_connection_and_access(self) -> bool:
        """Test the connection to Nextcloud and verify access."""
        if not self.data_source:
            return False

        try:
            response = await self.data_source.get_capabilities()

            if is_response_successful(response):
                self.logger.info("Nextcloud connection test successful.")
                return True
            else:
                self.logger.error(f"Connection test failed: {get_response_error(response)}")
                return False

        except Exception as e:
            self.logger.error(f"Connection test failed: {e}", exc_info=True)
            return False

    def handle_webhook_notification(self, notification: Dict) -> None:
        """Handle webhook notifications (not supported by Nextcloud)."""
        self.logger.warning(
            "Webhook notifications are not natively supported by Nextcloud. "
            "Use scheduled sync instead."
        )

    def _should_include_file(self, entry: Dict) -> bool:
        """
        Determines if a file should be included based on the file extension filter.

        Args:
            entry: Nextcloud file entry dict

        Returns:
            True if the file should be included, False otherwise
        """
        # Only filter files, not folders
        if entry.get('is_collection'):
            return True

        # Get the extensions filter
        extensions_filter = self.sync_filters.get(SyncFilterKey.FILE_EXTENSIONS)

        # If no filter configured or filter is empty, allow all files
        if extensions_filter is None or extensions_filter.is_empty():
            return True

        # Get the file extension from the entry path or display_name
        path = entry.get('path', '')
        display_name = entry.get('display_name', path.split('/')[-1] if path else '')
        
        file_extension = None
        if display_name and "." in display_name:
            file_extension = display_name.rsplit(".", 1)[-1].lower()

        # Handle files without extensions
        if file_extension is None:
            operator = extensions_filter.get_operator()
            operator_str = operator.value if hasattr(operator, 'value') else str(operator)
            return operator_str == FilterOperator.NOT_IN

        # Get the list of extensions from the filter value
        allowed_extensions = extensions_filter.value
        if not isinstance(allowed_extensions, list):
            return True  # Invalid filter value, allow the file

        # Normalize extensions (lowercase, without dots)
        normalized_extensions = [ext.lower().lstrip(".") for ext in allowed_extensions]

        # Apply the filter based on operator
        operator = extensions_filter.get_operator()
        operator_str = operator.value if hasattr(operator, 'value') else str(operator)

        if operator_str == FilterOperator.IN:
            # Only allow files with extensions in the list
            return file_extension in normalized_extensions
        elif operator_str == FilterOperator.NOT_IN:
            # Allow files with extensions NOT in the list
            return file_extension not in normalized_extensions

        # Unknown operator, default to allowing the file
        return True

    async def cleanup(self) -> None:
        """Clean up connector resources."""
        try:
            self.logger.info("Cleaning up Nextcloud connector resources.")

            # Clear cache
            self._path_to_external_id_cache.clear()

            # Clean up data source
            self.data_source = None

            # Clean up messaging producer
            if hasattr(self.data_entities_processor, 'messaging_producer'):
                messaging_producer = getattr(self.data_entities_processor, 'messaging_producer', None)
                if messaging_producer:
                    if hasattr(messaging_producer, 'cleanup'):
                        try:
                            await messaging_producer.cleanup()
                        except Exception as e:
                            self.logger.debug(f"Error cleaning up messaging producer: {e}")
                    elif hasattr(messaging_producer, 'stop'):
                        try:
                            await messaging_producer.stop()
                        except Exception as e:
                            self.logger.debug(f"Error stopping messaging producer: {e}")

        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}", exc_info=True)

    async def reindex_records(self, record_results: List[Record]) -> None:
        """Reindex records by fetching fresh metadata."""
        if not record_results:
            self.logger.info("No records to reindex")
            return

        self.logger.info(f"Starting reindex of {len(record_results)} records")

        reindexed_count = 0
        failed_count = 0

        for record in record_results:
            try:
                async with self.data_store_provider.transaction() as tx_store:
                    user_with_permission = await tx_store.get_first_user_with_permission_to_node(
                        f"{CollectionNames.RECORDS.value}/{record.id}"
                    )

                    file_record = await tx_store.get_file_record_by_id(record.id)

                if not user_with_permission or not file_record or not file_record.path:
                    failed_count += 1
                    continue

                async with self.rate_limiter:
                    response = await self.data_source.list_directory(
                        user_id=user_with_permission.source_user_id,
                        path=file_record.path,
                        depth="0"
                    )

                if not is_response_successful(response):
                    failed_count += 1
                    continue

                body = extract_response_body(response)
                if not body:
                    failed_count += 1
                    continue

                entries = parse_webdav_propfind_response(body)
                if not entries:
                    failed_count += 1
                    continue

                # Create empty cache for single record processing
                temp_cache = {}

                record_update = await self._process_nextcloud_entry(
                    entries[0],
                    user_with_permission.source_user_id,
                    user_with_permission.email,
                    file_record.external_record_group_id,
                    user_root_path=None,
                    path_to_external_id=temp_cache
                )

                if record_update and record_update.record:
                    await self.data_entities_processor.on_record_content_update(
                        record_update.record
                    )
                    reindexed_count += 1
                else:
                    failed_count += 1

                await asyncio.sleep(0.1)

            except Exception as e:
                self.logger.error(
                    f"Error reindexing record {record.id} ({record.record_name}): {e}",
                    exc_info=True
                )
                failed_count += 1

        self.logger.info(
            f"Reindex complete: {reindexed_count} successful, {failed_count} failed "
            f"out of {len(record_results)} total"
        )

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> NoReturn:
        """Nextcloud connector does not support dynamic filter options."""
        raise NotImplementedError("Nextcloud connector does not support dynamic filter options")

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str
    ) -> "BaseConnector":
        """Factory method to create a NextcloudConnector instance."""
        data_entities_processor = DataSourceEntitiesProcessor(
            logger, data_store_provider, config_service
        )
        await data_entities_processor.initialize()
        return NextcloudConnector(
            logger, data_entities_processor, data_store_provider, config_service, connector_id
        )