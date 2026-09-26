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
    PermissionModel,
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.constants import IconPaths
from app.connectors.core.base.connector.connector_service import (
    BaseConnector,
    ConnectorInitError,
)
from app.connectors.core.base.error.stream_errors import (
    connector_not_ready,
    map_source_status,
    to_stream_error,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import (
    DataStoreProvider,
)
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
    SyncStrategy,
)
from app.connectors.core.constants import CONNECTOR_EMAIL_IDENTITY_INFO
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
from app.services.notification.types import NotificationSeverity, NotificationType
from app.sources.client.nextcloud.nextcloud import (
    NextcloudClient,
    NextcloudRESTClientViaUsernamePassword,
)
from app.sources.external.nextcloud.nextcloud import NextcloudDataSource
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import get_epoch_timestamp_in_ms

NEXTCLOUD_PERM_MASK_ALL = 31
HTTP_STATUS_OK = 200
HTTP_STATUS_MULTIPLE_CHOICES = 300
HTTP_NOT_MODIFIED = 304
# Runs a page of activity is read again when part of it couldn't be applied, before moving on.
MAX_HELD_ATTEMPTS = 5
# How many of the changes that couldn't be applied a log line names before summarising the rest.
MAX_NAMED_FAILURES = 20

# Auth settings can't be saved while the connector is on, and only turning it on checks the password.
APP_PASSWORD_REJECTED_MESSAGE = (
    "Nextcloud rejected the app password, so nothing could be synced. Create a new app password "
    "in Nextcloud (Personal settings > Security > Devices & sessions). Then turn this connector "
    "off, enter the new password in its settings, save, and turn it back on."
)


class NextcloudAppPasswordRejectedError(ConnectorInitError):
    """Nextcloud answered 401: the app password was revoked, expired or mistyped."""


# Helper functions
def get_parent_path_from_path(path: str) -> Optional[str]:
    """Extracts the parent path from a file/folder path."""
    if not path or path == "/" or "/" not in path.lstrip("/"):
        return None
    parent_path = "/".join(path.strip("/").split("/")[:-1])
    return f"/{parent_path}" if parent_path else "/"


def path_inside_user_home(path: str, user_id: str | None) -> str:
    """``path`` relative to the user's home folder, without leading or trailing slashes.

    Only Nextcloud's own WebDAV prefix (``/remote.php/dav/files/<user>``, after any
    sub-path Nextcloud is installed under) is removed; a folder of the user's that
    happens to be called "files" stays part of the path.
    """
    prefix = f"/remote.php/dav/files/{user_id}"
    start = path.find(prefix) if user_id else -1
    if start != -1:
        rest = path[start + len(prefix):]
        if not rest or rest.startswith("/"):
            return rest.strip("/")
    return path.strip("/")


def describe_failures(failures: dict[str, str]) -> str:
    """``failures`` (what failed -> why) as one log-friendly line, naming at most MAX_NAMED_FAILURES."""
    named = [f"{item} ({reason})" for item, reason in list(failures.items())[:MAX_NAMED_FAILURES]]
    hidden = len(failures) - len(named)
    return "; ".join(named) + (f"; and {hidden} more" if hidden > 0 else "")


def pending_delete_fields(pending: list[str], paths: dict[str, str]) -> dict[str, list[str]]:
    """The checkpoint fields for queued deletions: IDs and their paths as parallel lists.

    Neo4j stores each field as a node property, which may be a list of strings
    but not a list of maps; both lists are written together so they stay aligned.
    """
    return {
        "pending_deletes": pending,
        "pending_delete_paths": [paths.get(i, "") for i in pending],
    }


def read_pending_deletes(checkpoint: dict) -> tuple[list[str], dict[str, str]]:
    """The queued deletion IDs and each one's path, from the fields ``pending_delete_fields`` writes."""
    ids = [str(i) for i in checkpoint.get("pending_deletes") or []]
    paths = [str(p or "") for p in checkpoint.get("pending_delete_paths") or []]
    return ids, dict(zip(ids, paths))


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
            share_list = [ocs_data] if ocs_data else []
        elif isinstance(ocs_data, list):
            share_list = ocs_data
        else:
            return shares

        for share_item in share_list:
            if not isinstance(share_item, dict):
                continue

            share = {}

            if 'share_type' in share_item:
                try:
                    share['share_type'] = int(share_item['share_type'])
                except (ValueError, TypeError):
                    logger = logging.getLogger(__name__)
                    logger.debug(f"Invalid share_type: {share_item.get('share_type')}")

            if 'share_with' in share_item:
                share_with_value = share_item['share_with']
                if share_with_value and isinstance(share_with_value, str):
                    share['share_with'] = share_with_value.strip()

            if 'permissions' in share_item:
                try:
                    perm_value = int(share_item['permissions'])
                    if 0 <= perm_value <= NEXTCLOUD_PERM_MASK_ALL:
                        share['permissions'] = perm_value
                    else:
                        share['permissions'] = 1
                except (ValueError, TypeError):
                    share['permissions'] = 1

            if share and ('share_type' in share or 'share_with' in share):
                shares.append(share)

    except json.JSONDecodeError as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to parse share response: {e}", exc_info=True)
        return []
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Error parsing share response: {e}", exc_info=True)
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
    elif permissions & 8 or permissions & 4 or permissions & 2:
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

    if hasattr(response, 'response'):
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
    .with_resilience_config(
        rate_limit=50,       # Nextcloud publishes no API budget; matches the connector's own listing limiter
        max_retries=3,       # 4 attempts total, waiting as long as Retry-After asks (capped at max_delay)
        base_delay=1.0,
        max_delay=60.0,
    )\
    .with_scopes([ConnectorScope.PERSONAL])\
    .with_permission_model(PermissionModel.APP_LEVEL)\
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
    .with_info(CONNECTOR_EMAIL_IDENTITY_INFO)\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.NEXTCLOUD.value))
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
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .add_filter_field(CommonFields.modified_date_filter("Filter files and folders by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter files and folders by creation date."))
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
        scope: str,
        created_by: str,
    ) -> None:
        super().__init__(
            NextcloudApp(connector_id=connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id=connector_id,
            scope=scope,
            created_by=created_by,
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
        self.base_url: Optional[str] = None

        self.data_source: Optional[NextcloudDataSource] = None
        self.batch_size = 100
        self.max_concurrent_batches = 5
        self.rate_limiter = AsyncLimiter(50, 1)
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

        # Cache for path-to-external-id mapping during sync
        self._path_to_external_id_cache: Dict[str, str] = {}

        # Cache for date filters (performance optimization)
        self._cached_date_filters: Tuple[Optional[datetime], Optional[datetime], Optional[datetime], Optional[datetime]] = (None, None, None, None)

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
                self.logger.error("Nextcloud 'baseUrl' is required in configuration. Checked auth_config, credentials_config, and root config")
                return False

            # Store base_url for later use (e.g., constructing web URLs)
            self.base_url = base_url.rstrip('/')

            username = auth_config.get("username")
            password = auth_config.get("password")

            if not username or not password:
                self.logger.error("Username and Password are required for Nextcloud")
                return False

            client = NextcloudRESTClientViaUsernamePassword(
                base_url, username, password, resilience=self.resilience
            )
            data_source = NextcloudDataSource(NextcloudClient(client))
            self.current_user_id = username

            self.current_user_email = await self._read_user_email(data_source)
            self.data_source = data_source
            self.logger.info(f"Nextcloud client initialized for user: {self.current_user_id}")
            return True
        except NextcloudAppPasswordRejectedError:
            raise
        except Exception as e:
            self.logger.error(f"Failed to initialize Nextcloud client: {e}", exc_info=True)
            return False

    async def _read_user_email(self, data_source: NextcloudDataSource) -> str | None:
        """The user's email from their Nextcloud profile, or None when the profile can't be read.

        Only a profile that was read and has no email gets the stand-in address;
        a failed read must not, or files are saved as owned by an address nobody has.
        """
        try:
            response = await data_source.get_user_details(self.current_user_id)
            await self._raise_if_app_password_rejected(response)
            if not is_response_successful(response):
                self.logger.warning(f"Could not read the Nextcloud profile: {get_response_error(response)}")
                return None
            user_data = json.loads(extract_response_body(response) or b"")["ocs"]["data"]
            return user_data.get("email") or f"{self.current_user_id}@nextcloud.local"
        except NextcloudAppPasswordRejectedError:
            raise
        except Exception as e:
            self.logger.warning(f"Could not read the Nextcloud profile: {e}")
            return None

    async def _raise_if_app_password_rejected(self, response: object) -> None:
        if getattr(response, "status", None) != HttpStatusCode.UNAUTHORIZED.value:
            return
        self.logger.error("❌ Nextcloud rejected the app password (HTTP 401)")
        await self.notify(
            type=NotificationType.CONNECTOR_AUTH_ERROR,
            severity=NotificationSeverity.ERROR,
            title="Nextcloud app password rejected",
            message=APP_PASSWORD_REJECTED_MESSAGE,
        )
        raise NextcloudAppPasswordRejectedError(APP_PASSWORD_REJECTED_MESSAGE)

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

        Returns None for an entry that is skipped; raises when it can't be processed.
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

            existing_record = await self.data_entities_processor.get_record_by_external_id(
                self.connector_id, file_id
            )

            # Detect changes
            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False

            # Store old parent and path for comparison (before resolution)
            old_parent_id = existing_record.parent_external_record_id if existing_record else None
            old_path = getattr(existing_record, 'path', None) if existing_record else None

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

                # A. Check if parent is the User Root
                if user_root_path and clean_parent_path == user_root_path:
                    parent_external_record_id = None

                # B. Check In-Memory Cache
                elif clean_parent_path in path_to_external_id:
                    parent_external_record_id = path_to_external_id[clean_parent_path]

                # C. Fallback to Database Lookup
                else:
                    try:
                        async with self.data_store_provider.transaction() as tx_store:
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

            # Detect parent or path changes (file/folder move)
            force_update = False
            if existing_record:
                # Check if parent changed (file/folder moved to different parent)
                if old_parent_id != parent_external_record_id:
                    metadata_changed = True
                    content_changed = True  # Re-index due to location context change
                    is_updated = True
                    force_update = True
                    self.logger.info(
                        f"📦 Parent changed for {display_name}: "
                        f"{old_parent_id or 'root'} -> {parent_external_record_id or 'root'}"
                    )

                # Check if path changed (covers renames within same parent or moves)
                if old_path is not None and old_path != path:
                    metadata_changed = True
                    content_changed = True  # Re-index due to location context change
                    is_updated = True
                    force_update = True
                    self.logger.info(f"📍 Path changed for {display_name}: {old_path} -> {path}")

                # Force etag change to ensure database update when parent/path changed
                if force_update and existing_record.external_revision_id == etag:
                    etag = f"{etag}-moved-{timestamp_ms}"
                    self.logger.debug(f"🔄 Modified etag to force update: {etag}")

            # Construct web URL for the file/folder
            web_url = ""
            if self.base_url and path:
                # Nextcloud web URLs follow the pattern: https://instance.com/f/{file_id} for files
                # and https://instance.com/f/{file_id} for folders as well
                web_url = f"{self.base_url}/f/{file_id}"

            is_file = not is_collection

            # Create FileRecord
            file_record = FileRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=display_name,
                record_type=record_type,
                record_group_type=RecordGroupType.DRIVE,
                external_record_group_id=record_group_id,
                external_record_id=file_id,
                external_revision_id=etag,
                version=0 if is_new else existing_record.version + 1,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.NEXTCLOUD,
                connector_id=self.connector_id,
                created_at=timestamp_ms,
                updated_at=timestamp_ms,
                source_created_at=timestamp_ms,
                source_updated_at=timestamp_ms,
                weburl=web_url,
                signed_url=None,
                parent_external_record_id=parent_external_record_id,
                size_in_bytes=size,
                is_file=is_file,
                preview_renderable=is_file,
                extension=get_file_extension(display_name) if is_file else "",
                path=None,  # Path derived at runtime via parent-child graph (get_record_path)
                mime_type=get_mimetype_enum_for_nextcloud(content_type, is_collection),
                etag=etag,
                ctag="",
                quick_xor_hash="",
                crc32_hash="",
                sha1_hash="",
                sha256_hash="",
            )

            if file_id:
                path_to_external_id[clean_path] = file_id
            owner_permission = [Permission(
                                email=user_email,
                                type=PermissionType.OWNER,
                                entity_type=EntityType.USER
            )]
            return RecordUpdate(
                record=file_record,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=False,
                metadata_changed=metadata_changed,
                content_changed=content_changed,
                permissions_changed=True,
                old_permissions=[],
                new_permissions=owner_permission,
                external_record_id=file_id
            )

        except Exception as ex:
            self.logger.error(
                f"Error processing entry {entry.get('file_id', entry.get('path'))}: {ex}",
                exc_info=True
            )
            raise

    async def _process_nextcloud_items_generator(
        self,
        entries: List[Dict],
        user_id: str,
        user_email: str,
        record_group_id: str,
        user_root_path: Optional[str],
        path_to_external_id: Dict[str, str],
        failed_entries: list[str],
    ) -> AsyncGenerator[Tuple[Optional[FileRecord], List[Permission], RecordUpdate], None]:
        """Process Nextcloud entries and yield records with their permissions.

        An entry that can't be processed is skipped and its id or path added to ``failed_entries``.
        """
        for entry in entries:
            try:
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
            except Exception:
                # Already logged by _process_nextcloud_entry; one bad entry doesn't stop the rest.
                failed_entries.append(str(entry.get('file_id') or entry.get('path')))

    async def _get_file_shares(self, path: str, user_id: str) -> List[Dict]:
        """
        Get shares for a file/folder path.
        Args:
            path: WebDAV path to the file/folder
            user_id: Current user ID
        Returns:
            List of share dictionaries
        """
        try:
            inside_home = path_inside_user_home(path, user_id)
            if not inside_home:
                return []
            relative_path = f"/{inside_home}"

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

            return parse_share_response(body)

        except Exception as e:
            self.logger.debug(f"Error fetching shares for {path}: {e}")
            return []

    async def _clear_parent_child_edges_for_records(
        self, records_with_permissions: List[Tuple[Record, List]]
    ) -> None:
        """Nextcloud single-parent: remove existing PARENT_CHILD edges so records don't appear in both old and new location."""
        if not records_with_permissions:
            return
        for record, _ in records_with_permissions:
            deleted = await self.data_entities_processor.delete_parent_child_edge_to_record(record.id)
            if deleted:
                self.logger.debug(
                    "Removed %d existing PARENT_CHILD edge(s) to %s (Nextcloud single-parent)",
                    deleted, record.id,
                )

    async def _handle_record_updates(self, record_update: RecordUpdate) -> bool:
        """Handle record updates (modified or deleted records). Follows Box connector pattern.

        Returns False when the change could not be saved.
        """
        try:
            if record_update.is_deleted:
                existing_record = await self.data_entities_processor.get_record_by_external_id(
                    self.connector_id, record_update.external_record_id
                )
                if existing_record:
                    await self.data_entities_processor.on_record_deleted(
                        record_id=existing_record.id
                    )

            elif record_update.is_updated:
                records_batch = [(record_update.record, record_update.new_permissions or [])]
                await self._clear_parent_child_edges_for_records(records_batch)
                await self.data_entities_processor.on_new_records(records_batch)

        except Exception as e:
            self.logger.error(f"Error handling record update: {e}", exc_info=True)
            return False
        return True

    async def _sync_user_files(
        self,
        user_id: str,
        user_email: str,
        record_group_id: str
    ) -> bool:
        """
        Synchronize all files for a specific user using WebDAV PROPFIND.
        Hardcoded depth to 100

        Returns True only when the whole drive was listed and every change saved.
        """
        try:
            self.logger.info(f"Syncing files for user: {user_email}")

            async with self.rate_limiter:
                response = await self.data_source.list_directory(
                    user_id=user_id,
                    path="",
                    depth=100
                )

            await self._raise_if_app_password_rejected(response)
            if not is_response_successful(response):
                self.logger.error(
                    f"Failed to list directory for {user_email}: {get_response_error(response)}"
                )
                return False

            body = extract_response_body(response)
            if not body:
                self.logger.error(f"Empty response for {user_email}")
                return False

            # Parse WebDAV response
            entries = parse_webdav_propfind_response(body)
            if not entries:
                # A readable listing always includes the home folder itself.
                self.logger.error(f"Could not read the file listing for {user_email}")
                return False

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
                return True

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
            all_saved = True
            failed_entries: list[str] = []

            # Pass correct variable name to generator
            async for file_record, permissions, record_update in self._process_nextcloud_items_generator(
                sorted_entries,
                user_id,
                user_email,
                record_group_id,
                user_root_path,
                path_to_external_id,
                failed_entries,
            ):
                # Handle updates separately from new records
                if record_update.is_updated and not record_update.is_new:
                    all_saved = await self._handle_record_updates(record_update) and all_saved
                    updated_count += 1
                    continue

                # Collect new records for batch processing
                if file_record and record_update.is_new:
                    batch_records.append((file_record, permissions))
                    batch_count += 1
                    new_count += 1

                    if batch_count >= self.batch_size:
                        self.logger.info(f"Processing batch of {batch_count} records")
                        await self._clear_parent_child_edges_for_records(batch_records)
                        await self.data_entities_processor.on_new_records(batch_records)
                        batch_records = []
                        batch_count = 0
                        await asyncio.sleep(0.1)

            # Process remaining records
            if batch_records:
                self.logger.info(f"Processing final batch of {len(batch_records)} records")
                await self._clear_parent_child_edges_for_records(batch_records)
                await self.data_entities_processor.on_new_records(batch_records)

            self.logger.info(
                f"Sync complete for {user_email}: {new_count} new, {updated_count} updated"
            )
            if failed_entries:
                self.logger.error(
                    f"❌ {len(failed_entries)} item(s) could not be processed: {', '.join(failed_entries[:20])}"
                )
                return False
            return all_saved

        except NextcloudAppPasswordRejectedError:
            raise
        except Exception as e:
            self.logger.error(f"Error syncing files for {user_email}: {e}", exc_info=True)
            return False

    async def run_sync(self) -> None:
        """
        Smart Sync: Automatically decides between Full vs. Incremental sync based on cursor state.
        - First run: Full sync + initialize cursor
        - Subsequent runs: Incremental sync using Activity API
        """
        try:
            self.logger.info("🔍 [Smart Sync] Starting Nextcloud sync...")

            # Load filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "nextcloud", self.connector_id, self.logger
            )

            # Cache date filters once at sync start for performance
            self._cached_date_filters = self._get_date_filters()

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

            if self.current_user_id and not self.current_user_email:
                self.current_user_email = await self._read_user_email(self.data_source)
            if not self.current_user_id or not self.current_user_email:
                self.logger.error(
                    "❌ Could not read the Nextcloud user's profile, so the owner of the files is unknown. "
                    "Nothing was synced; the next sync will try again."
                )
                return

            # 1. Check if we have an existing activity cursor
            sync_point_key = "activity_cursor"
            try:
                cursor_data = await self.activity_sync_point.read_sync_point(sync_point_key)
            except Exception:
                # A full sync in its place can't see deletions and would move the cursor past them.
                self.logger.error(
                    "❌ [Smart Sync] Could not read the saved activity cursor. This sync stops here "
                    "and the next one will try again."
                )
                raise

            # 2. DECISION LOGIC: Incremental vs Full
            if cursor_data and cursor_data.get('cursor'):
                cursor_val = cursor_data.get('cursor')
                self.logger.info(f"✅ [Smart Sync] Found existing cursor: {cursor_val}")
                self.logger.info("🚀 [Smart Sync] Switching to INCREMENTAL SYNC.")

                # Hand off to incremental sync
                await self._run_incremental_sync_internal()
                return

            # NO CURSOR FOUND
            self.logger.info("⚪ [Smart Sync] No cursor found. Starting FULL SYNC.")
            await self._run_full_sync_internal()

        except Exception as ex:
            self.logger.error(f"Error in Nextcloud Smart Sync: {ex}", exc_info=True)
            raise
        finally:
            # Clear cache after sync
            self._path_to_external_id_cache.clear()

    async def _run_full_sync_internal(self) -> None:
        """
        Internal method for full synchronization.
        """
        try:
            if not self.current_user_id or not self.current_user_email:
                self.logger.error("Current user info not available")
                return

            # Create a single app user for the current user
            app_user = AppUser(
                app_name=Connectors.NEXTCLOUD,
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
                connector_name=Connectors.NEXTCLOUD,
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
            if not await self._sync_user_files(
                self.current_user_id,
                self.current_user_email,
                self.current_user_id
            ):
                # The cursor only covers changes made after it, so saving one now would
                # leave whatever this run missed unsynced until it next changes.
                self.logger.error(
                    "❌ [Full Sync] The drive could not be read or saved in full. The activity cursor "
                    "was not saved, so the next sync runs a full sync again."
                )
                return

            # Initialize cursor for incremental sync
            # Fetch the latest activity ID to use as baseline for next incremental sync
            try:
                self.logger.info("⚓ [Full Sync] Anchoring activity cursor...")
                response = await self.data_source.get_activities(
                    activity_filter="files",
                    limit=1,
                    sort="desc"  # Get the most recent activity
                )

                if is_response_successful(response):
                    activities = self._parse_activity_response(response)
                    if activities:
                        latest_activity_id = activities[0].get('activity_id')
                        if latest_activity_id:
                            sync_point_key = "activity_cursor"
                            # The store merges writes, so a count left from an earlier cursor is reset here.
                            await self.activity_sync_point.update_sync_point(
                                sync_point_key,
                                {"cursor": str(latest_activity_id), "held_attempts": 0}
                            )
                            self.logger.info(f"⚓ [Full Sync] Anchored activity cursor to: {latest_activity_id}")
                    else:
                        self.logger.warning("⚠️ [Full Sync] No activities found to anchor cursor")
                else:
                    self.logger.warning(f"⚠️ [Full Sync] Failed to fetch activities for anchoring: {get_response_error(response)}")
            except Exception as e:
                self.logger.warning(f"❌ [Full Sync] Failed to anchor activity cursor: {e}")

            self.logger.info("✅ [Full Sync] Nextcloud full sync completed successfully.")

        except Exception as ex:
            self.logger.error(f"❌ [Full Sync] Error in full sync: {ex}", exc_info=True)
            raise

    async def _run_incremental_sync_internal(self) -> None:
        """
        Internal method for incremental synchronization using Activity API.
        """
        try:
            self.logger.info("🔄 [Incremental Sync] Starting incremental sync using Activity API...")

            if not self.data_source:
                self.logger.error("Data source not initialized")
                return

            # Get current user info
            if not self.current_user_id:
                self.logger.error("Current user ID not set")
                return

            if not self.current_user_email:
                self.logger.error("Current user email not known; nothing synced")
                return

            user_id = self.current_user_id
            user_email = self.current_user_email

            # Get existing record group
            existing_group = await self.data_entities_processor.get_record_group_by_external_id(
                self.connector_id, self.current_user_id
            )

            if not existing_group:
                self.logger.warning("⚠️ [Incremental Sync] Record group not found. Falling back to full sync.")
                await self._run_full_sync_internal()
                return

            # Get last activity cursor from sync point
            sync_point_key = "activity_cursor"
            sync_point_data = await self.activity_sync_point.read_sync_point(sync_point_key)
            last_activity_id = sync_point_data.get('cursor') if sync_point_data else None

            if last_activity_id is None:
                self.logger.warning("⚠️ [Incremental Sync] No cursor found. Falling back to full sync.")
                await self._run_full_sync_internal()
                return

            queued, pending_paths = read_pending_deletes(sync_point_data)
            pending_deletes = await self._retry_pending_deletes(sync_point_key, queued, pending_paths)

            self.logger.info(f"📋 [Incremental Sync] Fetching activities since ID: {last_activity_id}")

            # Fetch activities from Nextcloud Activity API
            response = await self.data_source.get_activities(
                activity_filter="files",
                since=int(last_activity_id),
                limit=500,  # Fetch up to 500 activities per sync
                sort="asc"  # Ascending order to process oldest first
            )

            # Check response status
            # HTTP 304 Not Modified means no new activities
            status_code = response.status

            if status_code == HTTP_NOT_MODIFIED:
                self.logger.info("✅ [Incremental Sync] HTTP 304 - No new activities. Database is up to date.")
                return

            await self._raise_if_app_password_rejected(response)

            if not is_response_successful(response):
                error_msg = get_response_error(response)
                # A full sync in its place can't see deletions and would move the cursor past them.
                self.logger.error(
                    f"❌ [Incremental Sync] Failed to fetch activities: {error_msg}. "
                    f"Status: {status_code or 'N/A'}. The cursor is kept, so the next sync reads "
                    "the same changes again."
                )
                return

            # Parse activity response
            activities = self._parse_activity_response(response)

            if not activities:
                self.logger.info("✅ [Incremental Sync] No new activities found. Database is up to date.")
                return

            self.logger.info(f"📋 [Incremental Sync] Found {len(activities)} new activities to process")

            # Extract unique file paths that were modified
            modified_paths = set()
            deleted_file_ids = set()
            deleted_paths: dict[str, str] = {}
            restored_paths: set[str] = set()
            max_activity_id = last_activity_id

            for activity in activities:
                activity_id = activity.get('activity_id')
                if activity_id and int(activity_id) > int(max_activity_id):
                    max_activity_id = activity_id

                # Check activity type
                activity_type = activity.get('type', '')
                object_type = activity.get('object_type', '')

                self.logger.debug(
                    f"Activity {activity_id}: type={activity_type}, "
                    f"object_type={object_type}, name={activity.get('object_name')}"
                )

                # Only process file-related activities
                if object_type == 'files':
                    # Nextcloud merges related events (several uploads at once)
                    # into one activity: object_id/object_name name only the
                    # first file, `objects` maps every file it covers.
                    objects = activity.get('objects')
                    if isinstance(objects, dict) and objects:
                        targets = list(objects.items())
                    else:
                        targets = [(activity.get('object_id'), activity.get('object_name', ''))]

                    if activity_type in ['file_deleted', 'file_trashed']:
                        for file_id, file_path in targets:
                            if file_id:
                                deleted_file_ids.add(str(file_id))
                                deleted_paths[str(file_id)] = file_path or ""
                                self.logger.info(f"🗑️  Deletion detected: {file_path} (ID: {file_id})")
                    elif activity_type in ['file_created', 'file_changed', 'file_renamed', 'file_restored']:
                        for _, file_path in targets:
                            if file_path:
                                modified_paths.add(file_path)
                                if activity_type == 'file_restored':
                                    restored_paths.add(file_path)
                                self.logger.info(f"📝 Modification detected: {file_path} ({activity_type})")

            failures: dict[str, str] = {}
            failed_deletes: dict[str, str] = {}
            found_ids: set[str] = set()

            # Process deletions
            if deleted_file_ids:
                self.logger.info(f"🗑️  [Incremental Sync] Processing {len(deleted_file_ids)} deletions")
                failed_deletes = await self._process_deletions(deleted_file_ids)
                for file_id, reason in failed_deletes.items():
                    failures[f"deletion of {deleted_paths.get(file_id) or 'file'} (ID {file_id})"] = reason

            # Process modifications and new files
            if modified_paths:
                self.logger.info(f"📝 [Incremental Sync] Processing {len(modified_paths)} modified/new files")
                failures.update(await self._process_modified_files(
                    list(modified_paths),
                    user_id,
                    user_email,
                    existing_group.external_group_id,
                    found_ids,
                    restored_paths,
                ))

            # The feed won't list these activities again once the cursor moves past them.
            if failures:
                held_attempts = int(sync_point_data.get("held_attempts") or 0) + 1
                if held_attempts < MAX_HELD_ATTEMPTS:
                    await self.activity_sync_point.update_sync_point(
                        sync_point_key,
                        {"cursor": str(last_activity_id), "held_attempts": held_attempts,
                         **pending_delete_fields(pending_deletes, pending_paths)},
                    )
                    self.logger.warning(
                        f"⚠️ [Incremental Sync] {len(failures)} change(s) could not be applied (attempt "
                        f"{held_attempts} of {MAX_HELD_ATTEMPTS}); the cursor stays at {last_activity_id} so "
                        f"the next sync reads them again: {describe_failures(failures)}"
                    )
                    return
                self.logger.error(
                    f"❌ [Incremental Sync] {len(failures)} change(s) still could not be applied after "
                    f"{held_attempts} attempts; moving on so later changes are not held up. Changed files "
                    "are picked up again when they next change in Nextcloud; deletions are retried at the "
                    f"start of every sync until they apply: {describe_failures(failures)}"
                )
                # A deleted file never changes again, so nothing else would bring its deletion back.
                # A later activity's file that Nextcloud listed just now was restored or
                # recreated. The activity type alone can't tell: one that 404s is still gone.
                pending_deletes = sorted(
                    set(pending_deletes) | {i for i in failed_deletes if i not in found_ids}
                )
                for file_id in pending_deletes:
                    # This page's deletion says where the file was last; a queued path may be older.
                    pending_paths[file_id] = deleted_paths.get(file_id) or pending_paths.get(file_id, "")

            # Update cursor to latest activity ID
            await self.activity_sync_point.update_sync_point(
                sync_point_key,
                {"cursor": str(max_activity_id), "held_attempts": 0,
                 **pending_delete_fields(pending_deletes, pending_paths)}
            )

            self.logger.info(
                f"✅ [Incremental Sync] Completed. Processed {len(modified_paths)} modified files, "
                f"{len(deleted_file_ids)} deletions. Cursor updated to {max_activity_id}"
            )

        except Exception as ex:
            self.logger.error(f"❌ [Incremental Sync] Error: {ex}", exc_info=True)
            # Don't fall back to full sync on every error - let the scheduler retry
            raise

    async def _retry_pending_deletes(
        self, sync_point_key: str, pending: list[str], pending_paths: dict[str, str]
    ) -> list[str]:
        """Apply the deletions an earlier run gave up on; returns the ones still owed.

        Each is applied only once Nextcloud confirms the file is gone, since it may
        have been restored after the deletion was queued. One whose record is
        already gone leaves the list.
        """
        if not pending:
            return []
        confirmed: set[str] = set()
        still_owed: dict[str, str] = {}
        for file_id in sorted(set(pending)):
            gone, reason = await self._is_gone_from_nextcloud(file_id, pending_paths.get(file_id, ""))
            if gone is None:
                still_owed[file_id] = reason
            elif gone:
                confirmed.add(file_id)
            else:
                self.logger.info(f"File {file_id} exists in Nextcloud again; its earlier deletion is dropped")
        if confirmed:
            still_owed.update(await self._process_deletions(confirmed))
        remaining = sorted(still_owed)
        if remaining != sorted(set(pending)):
            await self.activity_sync_point.update_sync_point(
                sync_point_key, pending_delete_fields(remaining, pending_paths)
            )
        if remaining:
            self.logger.warning(
                f"⚠️ [Incremental Sync] {len(remaining)} earlier deletion(s) still could not be applied; "
                f"they are retried next sync: {describe_failures({f'ID {i}': still_owed[i] for i in remaining})}"
            )
        return remaining

    async def _is_gone_from_nextcloud(self, file_id: str, deleted_path: str) -> tuple[bool | None, str]:
        """(True, "") when the file is gone, (False, "") when it is still there, (None, why) when unknown.

        ``deleted_path`` is where the deletion activity said the file was.
        """
        try:
            record = await self.data_entities_processor.get_record_by_external_id(self.connector_id, file_id)
            if record is None:
                children = await self.data_entities_processor.get_records_by_parent(
                    connector_id=self.connector_id, parent_external_record_id=file_id
                )
                if not children:
                    return True, ""
                # A cascade that committed partway left the folder's contents; the folder
                # may be back in Nextcloud, so it is checked where it was deleted from.
                if not deleted_path:
                    return None, "the folder's location is not known"
                path = deleted_path
            else:
                # A 404 proves the file gone only at its full stored path. The graph returns None
                # when the path read fails, and a bare name for a file with a parent isn't that path.
                path = await self.data_entities_processor.get_record_path(record.id)
                if not path:
                    return None, "could not read its stored path"
                if record.parent_external_record_id and "/" not in path.strip("/"):
                    return None, "its stored path is incomplete"
            async with self.rate_limiter:
                response = await self.data_source.list_directory(
                    user_id=self.current_user_id, path=path, depth=0
                )
            if getattr(response, "status", None) == HttpStatusCode.NOT_FOUND.value:
                return True, ""
            if not is_response_successful(response):
                return None, f"could not check Nextcloud: {get_response_error(response)}"
            body = extract_response_body(response)
            entries = parse_webdav_propfind_response(body) if body else []
            if not entries:
                return None, "could not read Nextcloud's answer"
            # Another file now at the same path means this one is still gone.
            return entries[0].get("file_id") != file_id, ""
        except Exception as e:
            return None, f"could not check Nextcloud: {str(e) or type(e).__name__}"

    async def run_incremental_sync(self) -> None:
        """
        Public method for manual incremental sync (kept for backward compatibility).
        Prefer using run_sync() which auto-detects sync mode.
        """
        self.logger.info("🔄 Manual incremental sync requested...")
        await self._run_incremental_sync_internal()

    def _parse_activity_response(self, response) -> List[Dict]:
        """
        Parse Nextcloud activity API response.
        Args:
            response: HTTP response from activity API
        Returns:
            List of activity dictionaries
        """
        activities = []

        try:
            response_body = extract_response_body(response)
            if not response_body:
                return activities

            data = json.loads(response_body)

            # OCS API response structure: {"ocs": {"meta": {...}, "data": [...]}}
            ocs_data = data.get('ocs', {}).get('data', [])

            if isinstance(ocs_data, list):
                for activity_item in ocs_data:
                    if not isinstance(activity_item, dict):
                        continue

                    activity = {
                        'activity_id': activity_item.get('activity_id'),
                        'type': activity_item.get('type'),
                        'object_type': activity_item.get('object_type'),
                        'object_id': activity_item.get('object_id'),
                        'object_name': activity_item.get('object_name'),
                        'objects': activity_item.get('objects'),
                        'datetime': activity_item.get('datetime'),
                        'subject': activity_item.get('subject'),
                    }

                    if activity.get('activity_id'):
                        activities.append(activity)

        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse activity response: {e}", exc_info=True)
        except Exception as e:
            self.logger.error(f"Unexpected error parsing activity response: {e}", exc_info=True)

        return activities

    async def _process_deletions(self, file_ids: set) -> dict[str, str]:
        """
        Process file deletions from activity feed.
        Args:
            file_ids: Set of external file IDs that were deleted
        Returns:
            The IDs whose deletion could not be applied, each with the reason; empty when all were
        """
        failed: dict[str, str] = {}
        try:
            for file_id in file_ids:
                try:
                    record = await self.data_entities_processor.get_record_by_external_id(
                        self.connector_id, str(file_id)
                    )

                    if record and record.mime_type != MimeTypes.FOLDER.value:
                        self.logger.info(f"Deleting record: {record.record_name} (ID: {file_id})")
                        await self.data_entities_processor.on_record_deleted(
                            record_id=record.id
                        )
                        continue

                    # Nextcloud logs one activity for a deleted folder, none for what it held.
                    if record:
                        root_ids = [record.id]
                    else:
                        # A cascade can commit partway, removing the folder but not all it held.
                        children = await self.data_entities_processor.get_records_by_parent(
                            connector_id=self.connector_id, parent_external_record_id=str(file_id)
                        )
                        root_ids = [child.id for child in children or []]
                    if not root_ids:
                        self.logger.debug(f"Record not found for deletion: {file_id}")
                        continue
                    result = await self.data_entities_processor.on_records_deleted_cascade(
                        root_ids, self.connector_id
                    )
                    # The graph reports a failed cascade in the result rather than raising.
                    if not result or not result.get("success") or result.get("failed_count"):
                        reason = (result or {}).get('reason') or str(result)
                        self.logger.error(
                            f"❌ Could not remove deleted folder {file_id} and everything in it: {reason}"
                        )
                        failed[str(file_id)] = f"folder delete failed: {reason}"
                        continue
                    self.logger.info(f"🗑️ Removed folder {file_id} and everything in it")

                except Exception as e:
                    self.logger.error(f"Error deleting record {file_id}: {e}", exc_info=True)
                    failed[str(file_id)] = str(e) or type(e).__name__

        except Exception as e:
            self.logger.error(f"Error processing deletions: {e}", exc_info=True)
            return dict.fromkeys(map(str, file_ids), str(e) or type(e).__name__)
        return failed

    async def _process_modified_files(
        self,
        file_paths: List[str],
        user_id: str,
        user_email: str,
        record_group_id: str,
        found_ids: set[str] | None = None,
        restored_paths: set[str] | None = None,
    ) -> dict[str, str]:
        """
        Process modified files by fetching their latest metadata.
        For incremental sync, sends new records immediately (no batching needed for small changes).
        Returns the paths that could not be fetched or saved, each with the reason; empty when all were.
        Adds the file ID of every entry Nextcloud listed to ``found_ids``. A folder in
        ``restored_paths`` is saved with everything below it.
        Args:
            file_paths: List of file paths that were modified
            user_id: User ID
            user_email: User email
            record_group_id: External record group ID
        """
        try:
            # Construct user root path to avoid unnecessary parent lookups
            # Format: /remote.php/dav/files/{user_id}
            user_root_path = f"/remote.php/dav/files/{user_id}"

            # Track processed parent folders to avoid duplicate fetches
            processed_parents = set()

            # Build a shared path-to-external-id map for all files in this batch
            # This allows parent folders created during processing to be found by children
            path_to_external_id = {}

            failed: dict[str, str] = {}
            for path in file_paths:
                try:
                    parents_ready = await self._ensure_parent_folders(
                        path,
                        user_id,
                        user_email,
                        record_group_id,
                        user_root_path,
                        path_to_external_id,
                        processed_parents,
                    )
                    if not parents_ready:
                        failed[path] = "a folder above it could not be read or saved"

                    # Now fetch and process the actual file
                    async with self.rate_limiter:
                        response = await self.data_source.list_directory(
                            user_id=user_id,
                            path=path,
                            depth=0  # Only fetch this item, not children
                        )

                    if getattr(response, "status", None) == HttpStatusCode.NOT_FOUND.value:
                        # Gone since the activity was logged; its deletion or move is in the feed too.
                        self.logger.debug(f"{path} no longer exists in Nextcloud")
                        continue

                    if not is_response_successful(response):
                        self.logger.warning(
                            f"Failed to fetch metadata for {path}: {get_response_error(response)}"
                        )
                        failed[path] = f"fetch failed: {get_response_error(response)}"
                        continue

                    # Parse response
                    response_body = extract_response_body(response)
                    entries = parse_webdav_propfind_response(response_body) if response_body else []
                    if not entries:
                        self.logger.warning(f"Could not read the metadata Nextcloud returned for {path}")
                        failed[path] = "Nextcloud's answer could not be read"
                        continue

                    # Build path-to-external-id map for this file and merge with existing map
                    file_path_map = await self._build_path_to_external_id_map(entries)
                    path_to_external_id.update(file_path_map)

                    # Process each entry
                    for entry in entries:
                        if found_ids is not None and entry.get('file_id'):
                            found_ids.add(str(entry['file_id']))
                        parent_lookup = path_to_external_id
                        if not parents_ready:
                            parent_lookup = await self._with_stored_parent(entry, path_to_external_id)
                        record_update = await self._process_nextcloud_entry(
                            entry=entry,
                            user_id=user_id,
                            user_email=user_email,
                            record_group_id=record_group_id,
                            user_root_path=user_root_path,
                            path_to_external_id=parent_lookup
                        )

                        if record_update:
                            # For incremental sync: send new records immediately, handle updates separately
                            if record_update.is_new and record_update.record:
                                await self.data_entities_processor.on_new_records(
                                    [(record_update.record, record_update.new_permissions or [])],
                                )
                            elif not await self._handle_record_updates(record_update):
                                failed[path] = "the change could not be saved"

                    # Nextcloud logs one activity for a restored folder and none for what it held,
                    # which the folder's deletion removed from the index.
                    if restored_paths and path in restored_paths and entries[0].get('is_collection'):
                        reason = await self._save_folder_contents(
                            path, user_id, user_email, record_group_id, user_root_path,
                            path_to_external_id, found_ids,
                        )
                        if reason:
                            failed[path] = reason

                except Exception as e:
                    self.logger.error(f"Error processing modified file {path}: {e}", exc_info=True)
                    failed[path] = str(e) or type(e).__name__

            return failed

        except Exception as e:
            self.logger.error(f"Error processing modified files: {e}", exc_info=True)
            return dict.fromkeys(file_paths, str(e) or type(e).__name__)

    async def _save_folder_contents(
        self,
        path: str,
        user_id: str,
        user_email: str,
        record_group_id: str,
        user_root_path: str,
        path_to_external_id: dict[str, str],
        found_ids: set[str] | None,
    ) -> str | None:
        """Save everything below the folder at ``path``; returns why it couldn't, or None."""
        async with self.rate_limiter:
            response = await self.data_source.list_directory(user_id=user_id, path=path, depth=100)
        if getattr(response, "status", None) == HttpStatusCode.NOT_FOUND.value:
            return None
        if not is_response_successful(response):
            return f"its contents could not be listed: {get_response_error(response)}"
        body = extract_response_body(response)
        entries = parse_webdav_propfind_response(body) if body else []
        if not entries:
            return "the listing of its contents could not be read"
        below = self._sort_entries_by_hierarchy(entries[1:])
        path_to_external_id.update(await self._build_path_to_external_id_map(below))
        if found_ids is not None:
            found_ids.update(str(e['file_id']) for e in below if e.get('file_id'))

        failed_entries: list[str] = []
        batch: list[tuple[FileRecord, list[Permission]]] = []
        async for record, permissions, update in self._process_nextcloud_items_generator(
            below, user_id, user_email, record_group_id, user_root_path, path_to_external_id, failed_entries,
        ):
            if update.is_new and record:
                batch.append((record, permissions))
                if len(batch) >= self.batch_size:
                    await self.data_entities_processor.on_new_records(batch)
                    batch = []
            elif update.is_updated and not await self._handle_record_updates(update):
                failed_entries.append(str(update.external_record_id))
        if batch:
            await self.data_entities_processor.on_new_records(batch)
        if failed_entries:
            return f"{len(failed_entries)} item(s) inside could not be saved"
        return None

    async def _with_stored_parent(self, entry: dict, path_to_external_id: dict[str, str]) -> dict[str, str]:
        """A copy of ``path_to_external_id`` that resolves the entry's folder to its stored parent.

        Used when the folder above the entry couldn't be read: its record would otherwise be
        saved with no parent, and a page given up on would leave it detached for good. The
        copy keeps a folder that may since have moved from being reused for other entries.
        """
        lookup = dict(path_to_external_id)
        parent_path = get_parent_path_from_path(entry.get('path', ''))
        if not parent_path or not entry.get('file_id'):
            return lookup
        existing = await self.data_entities_processor.get_record_by_external_id(
            self.connector_id, entry['file_id']
        )
        if existing and existing.parent_external_record_id:
            lookup.setdefault(parent_path.rstrip('/'), existing.parent_external_record_id)
        return lookup

    async def _ensure_parent_folders(
        self,
        path: str,
        user_id: str,
        user_email: str,
        record_group_id: str,
        user_root_path: str,
        path_to_external_id: Dict[str, str],
        processed_parents: set,
    ) -> bool:
        """Give every folder above ``path`` a record, top-down.

        A file can arrive inside folders the index has never seen. Records store
        no path to look a parent up by, so each folder is created right after
        the one above it, whose id is then in ``path_to_external_id``.
        Returns False when a folder that still exists could not be read or saved.
        Stops there: a folder created below it would have no parent, and a later
        run skips folders that already have a record, so it would stay detached.
        """
        parts = [p for p in path.strip("/").split("/") if p][:-1]
        for depth in range(1, len(parts) + 1):
            folder_path = "/" + "/".join(parts[:depth])
            if folder_path in processed_parents:
                continue
            try:
                async with self.rate_limiter:
                    response = await self.data_source.list_directory(
                        user_id=user_id, path=folder_path, depth=0
                    )
                if getattr(response, "status", None) == HttpStatusCode.NOT_FOUND.value:
                    continue
                if not is_response_successful(response):
                    self.logger.warning(
                        f"Failed to fetch folder {folder_path}: {get_response_error(response)}"
                    )
                    return False
                body = extract_response_body(response)
                entries = parse_webdav_propfind_response(body) if body else []
                if not entries or not entries[0].get('file_id'):
                    self.logger.warning(f"Could not read the folder Nextcloud returned for {folder_path}")
                    return False
                entry = entries[0]

                existing = await self.data_entities_processor.get_record_by_external_id(
                    self.connector_id, entry['file_id']
                )
                if existing is None:
                    update = await self._process_nextcloud_entry(
                        entry=entry,
                        user_id=user_id,
                        user_email=user_email,
                        record_group_id=record_group_id,
                        user_root_path=user_root_path,
                        path_to_external_id=path_to_external_id,
                    )
                    if update and update.record:
                        await self.data_entities_processor.on_new_records(
                            [(update.record, update.new_permissions or [])],
                        )
                        self.logger.info(f"📁 [Incremental Sync] Created folder: {folder_path}")

                path_to_external_id[entry.get('path', '').rstrip('/')] = entry['file_id']
                processed_parents.add(folder_path)
            except Exception as e:
                self.logger.warning(f"⚠️ [Incremental Sync] Failed to fetch/process folder {folder_path}: {e}")
                return False
        return True

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
            raise connector_not_ready(self.display_name)

        # Get file record and path (path may be stored or derived from parent-child graph)
        file_record = await self.data_entities_processor.get_file_record_by_id(record.id)
        path = None
        if file_record:
            path = await self.data_entities_processor.get_record_path(record.id)
        # Fallback: root-level or when graph path unavailable, use record name (WebDAV path = single segment)
        if file_record and (path is None or path.strip() == ""):
            path = record.record_name or file_record.record_name

        if not file_record or not path:
            self.logger.debug(
                "stream_record path resolution failed: record_id=%s file_record=%s path=%s",
                record.id, file_record is not None, path,
            )
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail="File not found or access denied"
            )

        # Check if it's a folder
        if file_record.mime_type == MimeTypes.FOLDER.value:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Cannot download folders"
            )

        relative_path = path_inside_user_home(path, self.current_user_id)

        # Download file using authenticated WebDAV client
        try:
            response = await self.data_source.download_file(
                user_id=self.current_user_id,
                path=relative_path
            )

            if not is_response_successful(response):
                self.logger.error(
                    f"Failed to download file for record {record.id}: {get_response_error(response)}"
                )
                raise map_source_status(
                    getattr(response, "status", None), connector=self.display_name
                )

            # Get file content from response
            file_content = extract_response_body(response)
            if not file_content:
                raise HTTPException(
                    status_code=HttpStatusCode.NOT_FOUND.value,
                    detail="Empty file content"
                )

            # Create async generator for streaming
            async def generate() -> AsyncGenerator[bytes, None]:
                yield file_content

            return create_stream_record_response(
                generate(),
                filename=record.record_name,
                mime_type=record.mime_type if record.mime_type else "application/octet-stream",
                fallback_filename=f"record_{record.id}"
            )
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Error streaming record {record.id}: {e}", exc_info=True)
            raise to_stream_error(e, connector=self.display_name) from e

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

    def _get_date_filters(self) -> Tuple[Optional[datetime], Optional[datetime], Optional[datetime], Optional[datetime]]:
        """
        Extract date filter values from sync_filters.

        Returns:
            Tuple of (modified_after, modified_before, created_after, created_before)
        """
        modified_after: Optional[datetime] = None
        modified_before: Optional[datetime] = None
        created_after: Optional[datetime] = None
        created_before: Optional[datetime] = None

        # Get modified date filter
        modified_date_filter = self.sync_filters.get(SyncFilterKey.MODIFIED)
        if modified_date_filter and not modified_date_filter.is_empty():
            after_iso, before_iso = modified_date_filter.get_datetime_iso()
            if after_iso:
                modified_after = datetime.fromisoformat(after_iso).replace(tzinfo=timezone.utc)
                self.logger.info(f"Applying modified date filter: after {modified_after}")
            if before_iso:
                modified_before = datetime.fromisoformat(before_iso).replace(tzinfo=timezone.utc)
                self.logger.info(f"Applying modified date filter: before {modified_before}")

        # Get created date filter
        created_date_filter = self.sync_filters.get(SyncFilterKey.CREATED)
        if created_date_filter and not created_date_filter.is_empty():
            after_iso, before_iso = created_date_filter.get_datetime_iso()
            if after_iso:
                created_after = datetime.fromisoformat(after_iso).replace(tzinfo=timezone.utc)
                self.logger.info(f"Applying created date filter: after {created_after}")
            if before_iso:
                created_before = datetime.fromisoformat(before_iso).replace(tzinfo=timezone.utc)
                self.logger.info(f"Applying created date filter: before {created_before}")

        return modified_after, modified_before, created_after, created_before

    def _should_include_file(self, entry: Dict) -> bool:
        """
        Determines if a file should be included based on the file extension filter and date filters.

        Args:
            entry: Nextcloud file entry dict

        Returns:
            True if the file should be included, False otherwise
        """
        # Only filter files, not folders
        if entry.get('is_collection'):
            return True

        # Get date filters from cache (performance optimization)
        modified_after, modified_before, created_after, created_before = self._cached_date_filters

        # Parse Nextcloud timestamps
        last_modified_str = entry.get('last_modified')
        modified_at = None

        if last_modified_str:
            try:
                modified_at = datetime.strptime(last_modified_str, "%a, %d %b %Y %H:%M:%S %Z")
                modified_at = modified_at.replace(tzinfo=timezone.utc)
            except Exception as e:
                self.logger.debug(f"Could not parse last_modified for {entry.get('display_name')}: {e}")

        # Nextcloud doesn't provide creation date via WebDAV, so we only apply modified date filters

        # Validate: If modified date filter is configured but file has no date, exclude it
        if modified_after or modified_before:
            if not modified_at:
                self.logger.debug(f"Skipping {entry.get('display_name')}: no modified date available")
                return False

        # Apply modified date filters
        if modified_at:
            if modified_after and modified_at < modified_after:
                self.logger.debug(f"Skipping {entry.get('display_name')}: modified {modified_at} before cutoff {modified_after}")
                return False
            if modified_before and modified_at > modified_before:
                self.logger.debug(f"Skipping {entry.get('display_name')}: modified {modified_at} after cutoff {modified_before}")
                return False

        # Note: Nextcloud WebDAV doesn't expose creation date, so created_after/created_before are not applied
        # If created date filters are set, log a warning once
        if (created_after or created_before) and not hasattr(self, '_created_filter_warning_logged'):
            self.logger.warning(
                "Created date filters are configured but Nextcloud WebDAV API does not provide creation dates. "
                "Only modification date filters will be applied."
            )
            self._created_filter_warning_logged = True

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
                user_with_permission = await self.data_entities_processor.get_first_user_with_permission_to_node(record.id, CollectionNames.RECORDS.value)
                file_record = await self.data_entities_processor.get_file_record_by_id(record.id)
                path = None
                if file_record:
                    path = await self.data_entities_processor.get_record_path(record.id)

                if file_record and (path is None or path.strip() == ""):
                    path = record.record_name or file_record.record_name

                if not user_with_permission or not file_record or not path:
                    failed_count += 1
                    continue

                async with self.rate_limiter:
                    response = await self.data_source.list_directory(
                        user_id=self.current_user_id,
                        path=path,
                        depth=0,
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

                # Records store no path, so the parent can't be looked up by one; the
                # stored path was just read at this location, so its stored parent holds.
                temp_cache = {}
                parent_path = get_parent_path_from_path(entries[0].get('path', ''))
                if parent_path and file_record.parent_external_record_id:
                    temp_cache[parent_path.rstrip('/')] = file_record.parent_external_record_id

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
        connector_id: str,
        scope: str,
        created_by: str,
        data_entities_processor,
        **kwargs,
    ) -> "BaseConnector":
        """Factory method to create a NextcloudConnector instance."""
        return NextcloudConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
