import asyncio
import dataclasses
import html
import json
import logging
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from logging import Logger
from typing import AsyncGenerator, Dict, List, NoReturn, Optional, Tuple
from urllib.parse import unquote
from xml.etree.ElementTree import ParseError

from aiolimiter import AsyncLimiter
from defusedxml import DefusedXmlException
from defusedxml import ElementTree as ET
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

# Base connector and service imports
from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    PermissionModel,
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
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
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperator,
    IndexingFilterKey,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate

# App-specific Nextcloud client imports
from app.connectors.sources.nextcloud.common.apps import NextcloudApp
from app.exceptions.graph_db_exceptions import GraphQueryError

# Model imports
from app.models.entities import (
    AppUser,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import Permission
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.notification.types import NotificationSeverity, NotificationType
from app.sources.client.nextcloud.nextcloud import (
    NextcloudClient,
    NextcloudRESTClientViaUsernamePassword,
)
from app.sources.external.nextcloud.nextcloud import NextcloudDataSource
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.utils.url_redaction import redact_urls_in_text

HTTP_STATUS_OK = 200
HTTP_STATUS_MULTIPLE_CHOICES = 300
HTTP_NOT_MODIFIED = 304
# Runs a page of activity is read again when part of it couldn't be applied, before moving on.
MAX_HELD_ATTEMPTS = 5
# How many of the changes that couldn't be applied a log line names before summarising the rest.
MAX_NAMED_FAILURES = 20
RECORD_SCAN_PAGE_SIZE = 1000
# The incremental sync reads every stream: a share or unshare is logged only outside the 'files' filter.
ACTIVITY_STREAM = "all"
# The most the Activity API returns per page, whatever is asked for.
ACTIVITY_PAGE_SIZE = 200
# Pages of activity one incremental run reads at most, before leaving the rest to the next run.
INCREMENTAL_TIME_BUDGET_SECONDS = 600
# A full listing and reconcile runs on the schedule this often, for what the activity feed never reports.
FULL_SYNC_INTERVAL_MS = 24 * 60 * 60 * 1000
# Folders listed at once by a full listing, each its own Depth 1 PROPFIND.
LISTING_CONCURRENCY = 8
DEFAULT_BATCH_SIZE = 100
# Only what the connector reads; the server computes each property for every entry.
SYNC_PROPERTIES = """
        <d:getlastmodified />
        <d:getetag />
        <d:getcontenttype />
        <d:resourcetype />
        <oc:fileid />
        <oc:size />
        <d:displayname />
        <nc:creation_time />
        <nc:share-attributes />
"""

# Auth settings can't be saved while the connector is on, and only turning it on checks the password.
APP_PASSWORD_REJECTED_MESSAGE = (
    "Nextcloud rejected the app password, so nothing could be synced. Create a new app password "
    "in Nextcloud (Personal settings > Security > Devices & sessions). Then turn this connector "
    "off, enter the new password in its settings, save, and turn it back on."
)
REMOTE_WIPE_MESSAGE = (
    "The app password was marked for remote wipe in Nextcloud, so everything this connector "
    "synced has been removed. To sync again, create a new app password, enter it in the "
    "connector's settings and turn the connector back on."
)
ACTIVITY_APP_MISSING_MESSAGE = (
    "The Nextcloud Activity app is not enabled, so changes can't be followed one by one. Each "
    "sync lists the whole drive instead, which is slower. Ask the Nextcloud admin to enable the "
    "Activity app."
)
NO_FILESYSTEM_ACCESS_MESSAGE = (
    "The Nextcloud app password does not allow filesystem access, so no files could be read and "
    "nothing was changed. In Nextcloud (Personal settings > Security > Devices & sessions), tick "
    "'Allow filesystem access' for this app password, or create a new one and enter it in this "
    "connector's settings."
)
FOLDERS_UNREADABLE_MESSAGE = (
    "Nextcloud could not read some folders, for example external storage or a share whose server "
    "is down. What was synced from them is kept, and they are read again at the next full sync, "
    "within a day: "
)
CHANGES_PARKED_MESSAGE = (
    "Some Nextcloud changes could not be applied after several attempts. They are retried on "
    "every sync until they apply; until then those files may be missing or out of date."
)
NEXTCLOUD_INFO = (
    "Syncs the files of one Nextcloud account, using an app password. Only you, the person who "
    "creates this connector, can search what it syncs. Create a dedicated app password in "
    "Nextcloud (Personal settings > Security > Devices & sessions) and use it here; never use "
    "your Nextcloud login password. The server URL must use https."
)


class NextcloudAppPasswordRejectedError(ConnectorInitError):
    """Nextcloud answered 401: the app password was revoked, expired or mistyped."""


class NextcloudNoFilesystemAccessError(ConnectorInitError):
    """The app password signs in but may not read files: Nextcloud shows the home as empty, with file id -1."""


class NextcloudSyncError(RuntimeError):
    """A sync that could not finish; raised so the run is reported as failed and nothing is unlinked."""


class NextcloudChangesHeldError(NextcloudSyncError):
    """Changes kept for the next run to apply; the user hears only once they are given up on."""


# Raised only after the user was told why, so a failed run does not tell them again.
ALREADY_NOTIFIED_ERRORS = (NextcloudAppPasswordRejectedError, NextcloudNoFilesystemAccessError, NextcloudChangesHeldError)
SABRE_MESSAGE = re.compile(rb"<s:message>(.*?)</s:message>", re.S)
MAX_REASON_CHARS = 300


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


class SkippedEntries:
    """Membership over the IDs a listing has skipped so far, while that list keeps growing."""

    def __init__(self, failed: list[str]) -> None:
        self._failed = failed
        self._read = 0
        self._seen: set[str] = set()

    def contains(self, entry_id: str | None) -> bool:
        if self._read < len(self._failed):
            self._seen.update(self._failed[self._read:])
            self._read = len(self._failed)
        return entry_id is not None and str(entry_id) in self._seen


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


def sync_batch_size(sync_config: object) -> int:
    """The Batch Size sync setting, or DEFAULT_BATCH_SIZE when it is unset or unreadable."""
    raw = sync_config.get("batchSize") if isinstance(sync_config, dict) else None
    try:
        return max(1, int(str(raw).strip())) if raw not in (None, "") else DEFAULT_BATCH_SIZE
    except ValueError:
        return DEFAULT_BATCH_SIZE


def full_sync_due(checkpoint: dict) -> bool:
    """Whether the last finished full listing is older than FULL_SYNC_INTERVAL_MS (or never ran)."""
    try:
        last = int(checkpoint.get("last_full_sync_ms") or 0)
    except (TypeError, ValueError):
        last = 0
    return get_epoch_timestamp_in_ms() - last >= FULL_SYNC_INTERVAL_MS


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

                    creation_elem = prop.find('nc:creation_time', namespaces)
                    if creation_elem is not None and (creation_elem.text or "").isdigit():
                        entry['creation_time'] = int(creation_elem.text)

                    attributes_elem = prop.find('nc:share-attributes', namespaces)
                    if attributes_elem is not None and attributes_elem.text:
                        entry['download_disabled'] = download_disabled(attributes_elem.text)

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

    except (ParseError, DefusedXmlException) as e:
        error_context = (
            redact_urls_in_text(xml_response[:500].decode('utf-8', errors='replace')) if xml_response else 'empty'
        )
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


def download_disabled(share_attributes: str) -> bool:
    """Whether a share's attributes (JSON) turn download off, as "Allow download" unticked does."""
    try:
        attributes = json.loads(share_attributes)
    except ValueError:
        return False
    return isinstance(attributes, list) and any(
        isinstance(a, dict)
        and a.get("scope") == "permissions"
        and a.get("key") == "download"
        # Nextcloud 30 renamed "enabled" to "value"; older servers still send "enabled".
        and (a.get("value") is False or a.get("enabled") is False)
        for a in attributes
    )


def is_unreadable(entry: dict) -> bool:
    """Whether Nextcloud could not open this node. It answers file id -1 (and an empty etag) both
    for an app password without filesystem access and for a mount whose storage is unavailable."""
    return not str(entry.get("file_id") or "").isdigit()


def is_multistatus(xml_response: bytes) -> bool:
    """Whether ``xml_response`` parses as a WebDAV multistatus, which an empty result still is."""
    try:
        return ET.fromstring(xml_response).tag == "{DAV:}multistatus"
    except (ParseError, DefusedXmlException):
        return False


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
    """The HTTP status and Nextcloud's own reason for a failed response, fit for a log line."""
    if hasattr(response, 'error') and response.error:
        return str(response.error)

    status = getattr(response, 'status', None) or getattr(response, 'status_code', None)
    if status is None:
        return "Unknown error"
    reason = nextcloud_reason(response)
    return f"HTTP {status}: {reason}" if reason else f"HTTP {status}"


def nextcloud_reason(response: object) -> str:
    """The message in a WebDAV (Sabre) or OCS error body, bounded and with URLs redacted; "" when there is none."""
    try:
        body = extract_response_body(response) or b""
        found = SABRE_MESSAGE.search(body)
        if found:
            text = html.unescape(found.group(1).decode("utf-8", errors="replace"))
        else:
            text = str(json.loads(body)["ocs"]["meta"].get("message") or "")
    except (ValueError, KeyError, TypeError, AttributeError):
        return ""
    return redact_urls_in_text(text.strip())[:MAX_REASON_CHARS]


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
                description="Your Nextcloud login name",
                required=True,
                min_length=1
            ),
            # 3. The App Password / Token
            # We use a password field so it is masked in the UI
            AuthField(
                name="password",
                display_name="App Password / Token",
                placeholder="e.g. xxxx-xxxx-xxxx-xxxx",
                description=(
                    "A dedicated app password from Nextcloud (Personal settings > Security > "
                    "Devices & sessions > Create new app password). Do not use your login password."
                ),
                field_type="PASSWORD",
                required=True,
                min_length=1,
                is_secret=True
            )
        ])
    ])\
    .with_info(NEXTCLOUD_INFO)\
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
        .add_filter_field(CommonFields.file_extension_filter())
        .add_filter_field(CommonFields.enable_manual_sync_filter())
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
        self._app_password: Optional[str] = None
        self.batch_size = DEFAULT_BATCH_SIZE
        self.rate_limiter = AsyncLimiter(50, 1)
        # Nextcloud throttles every request from an address that keeps failing to sign in, and all
        # connectors share PipesHub's address: after a 401 the password is not sent again. A new one
        # is saved with the connector off, and turning it on makes a new connector.
        self.app_password_rejected = False
        self._remote_wipe_asked = False
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()
        self._activity_app_missing_noted = False

        # Cache for date filters (performance optimization)
        self._cached_date_filters: Tuple[Optional[datetime], Optional[datetime]] = (None, None)

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

            username = auth_config.get("username")
            password = auth_config.get("password")

            if not username or not password:
                self.logger.error("Username and Password are required for Nextcloud")
                return False

            self.batch_size = sync_batch_size(config.get("sync"))
            try:
                client = NextcloudRESTClientViaUsernamePassword(
                    base_url, username, password, resilience=self.resilience
                )
            except ValueError as e:
                # The reason (https, '?', a blocked address) is what the person must fix.
                raise ConnectorInitError(str(e)) from e
            self.base_url = client.get_base_url()
            data_source = NextcloudDataSource(NextcloudClient(client))
            self._app_password = password

            self.current_user_id, self.current_user_email = await self._read_current_user(data_source)
            self.data_source = data_source
            self.logger.info(f"Nextcloud client initialized for user: {self.current_user_id}")
            return True
        except ConnectorInitError:
            raise
        except Exception as e:
            self.logger.error(f"Failed to initialize Nextcloud client: {e}", exc_info=True)
            return False

    async def _read_current_user(self, data_source: NextcloudDataSource) -> tuple[str | None, str | None]:
        """The signed-in user's Nextcloud id and profile email, or (None, None) when they can't be read.

        The id names the user's WebDAV and OCS paths; the login name typed into the form can
        differ from it (an email or LDAP login). The email is only used in log lines: access
        goes to the person who created the connector, never to whoever has this address.
        """
        try:
            response = await data_source.get_current_user()
            await self._raise_if_app_password_rejected(response, data_source)
            if not is_response_successful(response):
                self.logger.warning(f"Could not read the Nextcloud user: {get_response_error(response)}")
                return None, None
            user_data = json.loads(extract_response_body(response) or b"")["ocs"]["data"]
            user_id = str(user_data.get("id") or "") or None
            return user_id, user_data.get("email") or user_id
        except NextcloudAppPasswordRejectedError:
            raise
        except Exception as e:
            self.logger.warning(f"Could not read the Nextcloud user: {e}")
            return None, None

    async def _raise_if_app_password_rejected(
        self, response: object, data_source: NextcloudDataSource | None = None
    ) -> None:
        if getattr(response, "status", None) != HttpStatusCode.UNAUTHORIZED.value:
            return
        self.logger.error("❌ Nextcloud rejected the app password (HTTP 401)")
        self.app_password_rejected = True
        await self._app_password_rejected(data_source)

    async def _app_password_rejected(self, data_source: NextcloudDataSource | None = None) -> NoReturn:
        """Run a remote wipe Nextcloud asks for (asked once per rejected password), tell the user, and raise."""
        if not self._remote_wipe_asked:
            self._remote_wipe_asked = True
            if await self._remote_wipe_requested(data_source or self.data_source):
                await self._wipe_synced_data(data_source or self.data_source)
        await self.notify(
            type=NotificationType.CONNECTOR_AUTH_ERROR,
            severity=NotificationSeverity.ERROR,
            title=f"{self.display_name}: Nextcloud app password rejected",
            message=f"{self.display_name}: {APP_PASSWORD_REJECTED_MESSAGE}",
        )
        raise NextcloudAppPasswordRejectedError(APP_PASSWORD_REJECTED_MESSAGE)

    async def _raise_no_filesystem_access(self) -> NoReturn:
        self.logger.error("❌ Nextcloud answered file id -1 for the home folder: the app password has no filesystem access")
        await self.notify(
            type=NotificationType.CONNECTOR_AUTH_ERROR,
            severity=NotificationSeverity.ERROR,
            title=f"{self.display_name}: Nextcloud app password cannot read files",
            message=f"{self.display_name}: {NO_FILESYSTEM_ACCESS_MESSAGE}",
        )
        raise NextcloudNoFilesystemAccessError(NO_FILESYSTEM_ACCESS_MESSAGE)

    async def _remote_wipe_requested(self, data_source: NextcloudDataSource | None) -> bool:
        """Whether Nextcloud flagged this app password for remote wipe (asked after a 401, as its clients do)."""
        if data_source is None or not self._app_password:
            return False
        try:
            response = await data_source.check_remote_wipe_status(self._app_password)
            if not is_response_successful(response):
                return False
            return json.loads(extract_response_body(response) or b"{}").get("wipe") is True
        except Exception as e:
            self.logger.warning(f"Could not ask Nextcloud whether the app password is to be wiped: {e}")
            return False

    async def _wipe_synced_data(self, data_source: NextcloudDataSource) -> NoReturn:
        """Remove every record this connector synced, then tell Nextcloud the wipe is done.

        Nextcloud deletes the app password once told, so that is only done when every record
        is gone; otherwise the next sync's 401 asks again and finishes the job.
        """
        self.logger.warning("Nextcloud asked for a remote wipe: removing everything this connector synced")
        removed_all = await self._remove_records_not_listed(set())
        self._remote_wipe_asked = removed_all
        # A sync with a new app password must list the drive again, not resume the feed.
        await self.activity_sync_point.update_sync_point("activity_cursor", {"cursor": "", "last_full_sync_ms": 0})
        if removed_all:
            try:
                await data_source.signal_remote_wipe_success(self._app_password)
            except Exception as e:
                self.logger.warning(f"Could not tell Nextcloud the wipe is done: {e}")
        await self.notify(
            type=NotificationType.CONNECTOR_AUTH_ERROR,
            severity=NotificationSeverity.ERROR,
            title=f"{self.display_name}: synced Nextcloud data removed",
            message=f"{self.display_name}: {REMOTE_WIPE_MESSAGE}",
        )
        raise NextcloudAppPasswordRejectedError(REMOTE_WIPE_MESSAGE)

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
                # Folder hrefs end in '/', and parents are looked up without it.
                path_map[path.rstrip('/')] = file_id

        return path_map

    async def _stored_folder_id_at(self, names: list[str], record_group_id: str) -> str | None:
        """External id of the stored folder reached by ``names`` from the top of the drive, or None.

        The graph store hands back the stored document, not a Record. A read that fails raises.
        """
        async with self.data_store_provider.transaction() as tx_store:
            stored = await tx_store.get_record_by_path(
                connector_id=self.connector_id,
                path=names,
                external_record_group_id=record_group_id,
            )
        return stored.get("externalRecordId") if stored else None

    async def _process_nextcloud_entry(
        self,
        entry: Dict,
        user_id: str,
        user_email: str,
        record_group_id: str,
        user_root_path: Optional[str],
        path_to_external_id: Dict[str, str],
        stored_records: dict[str, Record] | None = None,
    ) -> Optional[RecordUpdate]:
        """
        Process a single Nextcloud entry and detect changes.

        ``stored_records``: every stored record by external id, read once by a full
        listing; without it each entry is looked up on its own.

        Returns None for an entry that is skipped; raises when it can't be processed.
        """
        try:
            # Extract basic properties
            file_id = entry.get('file_id')
            if not file_id:
                return None
            if is_unreadable(entry):
                # Saved, it would be a record keyed "-1" that every unreadable folder shares.
                raise NextcloudSyncError(f"Nextcloud could not open {entry.get('path')}: its storage is not available")

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

            if stored_records is not None:
                existing_record = stored_records.get(str(file_id))
            else:
                existing_record = await self.data_entities_processor.get_record_by_external_id(
                    self.connector_id, file_id
                )

            # Detect changes
            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False

            old_parent_id = existing_record.parent_external_record_id if existing_record else None

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
                    folder_in_home = path_inside_user_home(clean_parent_path, user_id or self.current_user_id)
                    if folder_in_home:
                        parent_external_record_id = await self._stored_folder_id_at(
                            folder_in_home.split("/"), record_group_id
                        )
                        if parent_external_record_id:
                            path_to_external_id[clean_parent_path] = parent_external_record_id
                        else:
                            self.logger.warning(
                                f"No stored folder at {parent_path}; saving {display_name} without a parent."
                            )

            # A move keeps the etag; it is saved in place (see _handle_record_updates), not re-indexed.
            if existing_record and old_parent_id != parent_external_record_id:
                metadata_changed = True
                is_updated = True
                self.logger.info(
                    f"📦 Parent changed for {display_name}: "
                    f"{old_parent_id or 'root'} -> {parent_external_record_id or 'root'}"
                )

            # /index.php/ works whether or not the server has pretty URLs set up.
            web_url = f"{self.base_url}/index.php/f/{file_id}" if self.base_url and path else ""

            is_file = not is_collection
            # Nextcloud has a creation time only when the uploading client sent one (0 otherwise),
            # so the first time seen is kept rather than moving to each new modified time.
            created_ms = entry.get('creation_time', 0) * 1000 or (
                existing_record.source_created_at if existing_record and existing_record.source_created_at
                else timestamp_ms
            )

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
                created_at=created_ms,
                updated_at=timestamp_ms,
                source_created_at=created_ms,
                source_updated_at=timestamp_ms,
                weburl=web_url,
                signed_url=None,
                parent_external_record_id=parent_external_record_id,
                parent_record_type=RecordType.FILE if parent_external_record_id else None,
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

            if is_file and not self.indexing_filters.is_enabled(IndexingFilterKey.FILES, default=True):
                file_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
            if file_id:
                path_to_external_id[clean_path] = file_id
            file_record.inherit_permissions = True
            file_record.rewrite_permissions = True
            return RecordUpdate(
                record=file_record,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=False,
                metadata_changed=metadata_changed,
                content_changed=content_changed,
                permissions_changed=True,
                old_permissions=[],
                new_permissions=[],
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
        stored_records: dict[str, Record] | None = None,
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
                    path_to_external_id,
                    stored_records,
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

            elif record_update.is_updated and record_update.metadata_changed and not record_update.content_changed:
                # A rename or move: Nextcloud keeps the file id and etag, so the record keeps its
                # indexing state and is re-indexed only if its file type changed.
                await self.data_entities_processor.on_records_moved(
                    [(record_update.external_record_id, record_update.record, record_update.new_permissions or [])]
                )

            elif record_update.is_updated:
                records_batch = [(record_update.record, record_update.new_permissions or [])]
                await self.data_entities_processor.on_new_records(records_batch)

        except Exception as e:
            self.logger.error(f"Error handling record update: {e}", exc_info=True)
            return False
        return True

    async def _sync_user_files(
        self,
        user_id: str,
        user_email: str,
        record_group_id: str,
        resave: set[str] | None = None,
        listed: set[str] | None = None,
        *,
        rewrite_unchanged: bool = False,
        unread: dict[str, str] | None = None,
        returned: set[str] | None = None,
    ) -> bool:
        """
        Synchronize all files for a specific user, listing the drive folder by folder.

        ``resave`` holds the IDs of stored records to save again whatever their state. Each
        one saved leaves it, and each stored record whose save fails joins it.

        ``listed`` gathers the IDs of the files and folders the listing returned that the
        sync filters keep.

        ``rewrite_unchanged`` saves unchanged records again too, which restores links an
        earlier run removed (a full sync that failed partway used to unlink what it missed).

        ``unread`` gathers the folders Nextcloud would not list (path -> why). What is stored
        for them and below them is kept as it is, and counted in ``listed``.

        ``returned`` gathers the IDs of everything the listing returned, filtered out or not.

        Returns True only when the drive was listed, apart from ``unread``, and every change saved.
        """
        try:
            self.logger.info(f"Syncing files for user: {user_email}")

            try:
                entries, unreadable = await self._list_tree(user_id, "")
            except (NextcloudAppPasswordRejectedError, NextcloudNoFilesystemAccessError, NextcloudSyncError):
                raise
            except Exception as e:
                self.logger.error(f"❌ Could not list the drive of {user_email}: {e}")
                return False

            # 1. Capture the Root Path, e.g. /remote.php/dav/files/NC_Admin
            user_root_path = entries[0].get('path', '').rstrip('/')
            entries = entries[1:]
            if returned is not None:
                returned.update(str(e['file_id']) for e in entries if e.get('file_id'))

            self.logger.info(f"Found {len(entries)} entries for {user_email}")

            stored_records = await self._stored_records() if entries or unreadable else {}
            if unreadable:
                kept = await self._stored_records_below(list(unreadable), record_group_id, stored_records)
                self.logger.warning(
                    f"⚠️ {len(unreadable)} folder(s) could not be read; their {len(kept)} stored record(s) "
                    f"are kept as they are: {describe_failures(unreadable)}"
                )
                if listed is not None:
                    listed.update(str(r.external_record_id) for r in kept)
                self.data_entities_processor.note_kept_records(self.connector_id, [r.id for r in kept])
                if unread is not None:
                    unread.update(unreadable)

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
            updated_count = 0
            new_count = 0
            all_saved = True
            failed_entries: list[str] = []
            skipped = SkippedEntries(failed_entries)
            new_folder_ids: set[str] = set()

            async def flush() -> None:
                nonlocal batch_records
                if batch_records:
                    # New or unchanged records only: neither has a parent link to clear.
                    self.logger.info(f"Processing batch of {len(batch_records)} records")
                    await self.data_entities_processor.on_new_records(batch_records)
                    batch_records = []

            async for file_record, permissions, record_update in self._process_nextcloud_items_generator(
                sorted_entries,
                user_id,
                user_email,
                record_group_id,
                user_root_path,
                path_to_external_id,
                failed_entries,
                stored_records,
            ):
                if listed is not None and record_update.external_record_id:
                    listed.add(str(record_update.external_record_id))
                parent_id = file_record.parent_external_record_id if file_record else None
                # A save links a record only to a parent that is already stored, and a later
                # full sync sees nothing to change, so what's inside a skipped folder waits too.
                if skipped.contains(parent_id):
                    failed_entries.append(str(record_update.external_record_id))
                    continue

                if record_update.is_new:
                    if not file_record:
                        continue
                    batch_records.append((file_record, permissions))
                    new_count += 1
                    if not file_record.is_file:
                        new_folder_ids.add(str(record_update.external_record_id))
                    if len(batch_records) >= self.batch_size:
                        await flush()
                        await asyncio.sleep(0.1)
                    continue

                # A stored record under a folder that is new in this run is saved again so it is
                # linked under that folder, which a failed earlier run may have left unstored.
                # So is one whose save failed in an earlier run: that save had already removed
                # its parent link, and nothing about it looks changed now.
                record_id = str(record_update.external_record_id)
                relink = (parent_id is not None and parent_id in new_folder_ids) or (
                    resave is not None and record_id in resave
                )
                if not (record_update.is_updated or relink):
                    if file_record and rewrite_unchanged:
                        # Same parent as stored, so no link to clear: batched like new records.
                        batch_records.append((file_record, permissions))
                        if len(batch_records) >= self.batch_size:
                            await flush()
                        continue
                    # Unchanged and not written: without this the unlink after a full
                    # sync cuts its only path to the app.
                    if file_record:
                        self.data_entities_processor.note_kept_records(self.connector_id, [file_record.id])
                    continue
                await flush()  # folders sort first, so any new folder above this one is stored
                update = record_update if record_update.is_updated else dataclasses.replace(
                    record_update, is_updated=True
                )
                saved = await self._handle_record_updates(update)
                all_saved = saved and all_saved
                if resave is not None:
                    (resave.discard if saved else resave.add)(record_id)
                updated_count += 1

            # Process remaining records
            await flush()

            self.logger.info(
                f"Sync complete for {user_email}: {new_count} new, {updated_count} updated"
            )
            if failed_entries:
                self.logger.error(
                    f"❌ {len(failed_entries)} item(s) could not be processed: {', '.join(failed_entries[:20])}"
                )
                return False
            return all_saved

        except (NextcloudAppPasswordRejectedError, NextcloudNoFilesystemAccessError):
            raise
        except Exception as e:
            self.logger.error(f"Error syncing files for {user_email}: {e}", exc_info=True)
            return False

    async def _list_folder(self, user_id: str, path: str) -> list[dict] | None:
        """One folder's entry followed by its children's (Depth 1); None when Nextcloud says it is gone.

        Raises when the folder could not be listed or its answer read.
        """
        async with self.rate_limiter:
            response = await self.data_source.list_directory(
                user_id=user_id, path=path, depth=1, properties=SYNC_PROPERTIES
            )
        await self._raise_if_app_password_rejected(response)
        if getattr(response, "status", None) == HttpStatusCode.NOT_FOUND.value:
            return None
        if not is_response_successful(response):
            raise NextcloudSyncError(f"listing /{path} failed: {get_response_error(response)}")
        body = extract_response_body(response)
        entries = parse_webdav_propfind_response(body) if body else []
        if not entries:
            # A readable listing always includes the folder itself.
            raise NextcloudSyncError(f"the listing of /{path} could not be read")
        if is_unreadable(entries[0]):
            if not path.strip("/"):
                # Read as an empty drive, it would remove every record.
                await self._raise_no_filesystem_access()
            raise NextcloudSyncError(f"Nextcloud could not open /{path}: its storage is not available")
        return entries

    async def _list_tree(self, user_id: str, path: str) -> tuple[list[dict], dict[str, str]]:
        """The folder at ``path`` (relative to the home) and everything below it, the folder first,
        and the subfolders that could not be read (path in the home -> why).

        One Depth 1 PROPFIND per folder, LISTING_CONCURRENCY at a time, so no answer holds more
        than one folder and there is no depth limit. A subfolder deleted while the walk runs is
        left out, one that moved is listed where it now is, and one Nextcloud answers for with an
        error or file id -1 (storage that is unavailable) goes in the unread folders. Any other
        failure raises (FileNotFoundError when ``path`` itself is gone).
        """
        top = await self._list_folder(user_id, path)
        if top is None:
            raise FileNotFoundError(f"/{path} is not in Nextcloud")
        entries = [top[0]]
        unread: dict[str, str] = {}
        # A folder that moved while the walk runs can turn up again in a folder listed after the move.
        seen = {str(top[0]["file_id"])} if top[0].get("file_id") else set()
        level = self._add_readable(top, entries, unread, user_id, seen)
        while level:
            folders, level = level, []
            for start in range(0, len(folders), LISTING_CONCURRENCY):
                chunk = folders[start:start + LISTING_CONCURRENCY]
                answers = await asyncio.gather(*(self._list_subfolder(user_id, f) for f in chunk))
                for folder, children in zip(chunk, answers):
                    if isinstance(children, str):
                        unread["/" + path_inside_user_home(folder.get("path", ""), user_id)] = children
                        continue
                    if not children:
                        entries.remove(folder)
                        continue
                    level.extend(self._add_readable(children, entries, unread, user_id, seen))
        return entries, unread

    @staticmethod
    def _add_readable(
        listing: list[dict], entries: list[dict], unread: dict[str, str], user_id: str, seen: set[str]
    ) -> list[dict]:
        """Add the entries below a folder's own to ``entries`` (once per file id in ``seen``), and
        the unreadable ones to ``unread``; returns the readable folders among those added."""
        folders = []
        for entry in listing[1:]:
            if is_unreadable(entry):
                unread["/" + path_inside_user_home(entry.get("path", ""), user_id)] = "its storage is not available"
                continue
            file_id = str(entry.get("file_id") or "")
            if file_id:
                if file_id in seen:
                    continue
                seen.add(file_id)
            entries.append(entry)
            if entry.get("is_collection"):
                folders.append(entry)
        return folders

    async def _list_subfolder(self, user_id: str, folder: dict) -> list[dict] | str:
        """A subfolder's listing (from where it now is, if it moved), [] when Nextcloud confirms
        it gone, or why Nextcloud would not list it."""
        try:
            listing = await self._list_folder(user_id, path_inside_user_home(folder.get("path", ""), user_id))
            if listing is None:
                listing = await self._list_moved_folder(user_id, folder)
            return listing
        except NextcloudSyncError as e:
            self.logger.warning(f"⚠️ Could not list {folder.get('path')}: {e}")
            return str(e)

    async def _list_moved_folder(self, user_id: str, folder: dict) -> list[dict]:
        """The listing of a folder whose listed path now 404s, taken where it now is; [] once a
        lookup by ID confirms it gone. Raises when neither can be told.

        A 404 alone can't tell a rename from a deletion, and read as a deletion it would remove
        everything stored below a folder renamed while the drive was listed. The folder's own
        entry is updated in place, so it is saved where it now is.
        """
        path = path_inside_user_home(folder.get("path", ""), user_id)
        file_id = str(folder.get("file_id") or "")
        found, href = await self._find_in_nextcloud_by_id(file_id) if file_id else (None, "")
        if found is False:
            return []
        if not found:
            raise NextcloudSyncError(
                f"/{path} moved or was deleted while the drive was listed, and it could not be looked up by ID"
            )
        listing = await self._list_folder(user_id, path_inside_user_home(href, user_id))
        if listing is None or str(listing[0].get("file_id") or "") != file_id:
            raise NextcloudSyncError(f"/{path} moved again while the drive was listed")
        folder.update(listing[0])
        return listing

    async def _stored_records_below(
        self, folders: list[str], record_group_id: str, stored: dict[str, Record]
    ) -> list[Record]:
        """The stored records of the folders at these paths and of everything below them."""
        children: dict[str, list[Record]] = defaultdict(list)
        for record in stored.values():
            if record.parent_external_record_id:
                children[str(record.parent_external_record_id)].append(record)
        found: list[Record] = []
        for folder in folders:
            folder_id = await self._stored_folder_id_at(folder.strip("/").split("/"), record_group_id)
            pending = [stored[folder_id]] if folder_id in stored else []
            while pending:
                record = pending.pop()
                found.append(record)
                pending.extend(children.get(str(record.external_record_id), []))
        return found

    async def _stored_records(self) -> dict[str, Record]:
        """Every live record of this connector by external id, read in pages (one query per page, not per file)."""
        stored: dict[str, Record] = {}
        after_key: str | None = None
        while True:
            page = await self.data_entities_processor.get_records_by_status(
                self.connector_id, None, limit=RECORD_SCAN_PAGE_SIZE, after_key=after_key,
            )
            stored.update((str(r.external_record_id), r) for r in page)
            if len(page) < RECORD_SCAN_PAGE_SIZE:
                return stored
            after_key = page[-1].id

    async def _remove_records_not_listed(
        self, listed: set[str], returned: set[str] | None = None, unread: dict[str, str] | None = None
    ) -> bool:
        """Delete the records of files and folders a complete listing no longer returns.

        After a full sync that read the whole drive, a record the listing did not
        return is a file deleted in Nextcloud or one the sync filters now leave
        out, so it leaves every store. Only called once the drive was read in full.
        With ``returned`` (every ID the listing held), one Nextcloud left out is removed
        only once a lookup at its stored path proves it gone; the others, and what is
        stored below them, are kept and added to ``unread``.
        Returns False when the records could not be read or any delete failed.
        """
        stale: list[Record] = []
        after_key: str | None = None
        try:
            while True:
                # Trash included: a trashed record the drive no longer has is removed too.
                page = await self.data_entities_processor.get_records_by_status(
                    self.connector_id, None, limit=RECORD_SCAN_PAGE_SIZE, after_key=after_key,
                    visibility=RecordVisibility.ALL,
                )
                stale.extend(r for r in page if r.external_record_id not in listed)
                if len(page) < RECORD_SCAN_PAGE_SIZE:
                    break
                after_key = page[-1].id
        except Exception as e:
            self.logger.error(f"❌ [Full Sync] Could not read the synced records, so none are removed: {e}")
            return False

        if returned is not None and stale:
            kept = await self._left_out_but_not_gone(
                [r for r in stale if str(r.external_record_id) not in returned], unread
            )
            if kept:
                self.data_entities_processor.note_kept_records(self.connector_id, [r.id for r in kept])
                kept_ids = {r.id for r in kept}
                stale = [r for r in stale if r.id not in kept_ids]

        if stale:
            self.logger.info(
                f"[Full Sync] Removing {len(stale)} record(s) no longer in Nextcloud or in the sync filters"
            )
        removed_all = True
        for record in stale:
            try:
                await self.data_entities_processor.on_record_deleted(record_id=record.id)
            except Exception as e:
                removed_all = False
                self.logger.warning(f"Failed to remove record {record.external_record_id}: {e}")
        return removed_all

    async def _left_out_but_not_gone(self, missing: list[Record], unread: dict[str, str] | None) -> list[Record]:
        """Of the records the listing left out, those not proven gone, with everything below them.

        The first listing after a mount's storage goes offline leaves the mount out altogether
        (seen live, Nextcloud 31), and a search by ID misses it too; only its stored path
        answers, with file id -1. So the topmost of them is looked up there, and only a 404,
        or another file in its place, counts as gone.
        """
        by_id = {str(r.external_record_id): r for r in missing}
        kept_top: set[str] = set()
        for record in missing:
            if str(record.parent_external_record_id or "") in by_id:
                continue
            path, reason = await self._still_at_stored_path(record)
            if reason:
                kept_top.add(str(record.external_record_id))
                self.logger.warning(f"⚠️ [Full Sync] Keeping {path or record.record_name}: {reason}")
                if unread is not None:
                    unread[path or record.record_name] = reason

        def below_kept(record: Record) -> bool:
            seen: set[str] = set()
            current: Record | None = record
            while current is not None and str(current.external_record_id) not in seen:
                if str(current.external_record_id) in kept_top:
                    return True
                seen.add(str(current.external_record_id))
                current = by_id.get(str(current.parent_external_record_id or ""))
            return False

        return [r for r in missing if below_kept(r)]

    async def _still_at_stored_path(self, record: Record) -> tuple[str, str]:
        """(its stored path, why it may still be there), with an empty why when Nextcloud proves it gone."""
        try:
            path = await self.data_entities_processor.get_record_path(record.id)
            if not path or (record.parent_external_record_id and "/" not in path.strip("/")):
                return path or "", "left out of the listing, and its stored path could not be read"
            async with self.rate_limiter:
                response = await self.data_source.list_directory(
                    user_id=self.current_user_id, path=path, depth=0, properties=SYNC_PROPERTIES
                )
            if getattr(response, "status", None) == HttpStatusCode.NOT_FOUND.value:
                return path, ""
            if not is_response_successful(response):
                return path, f"left out of the listing, and Nextcloud could not be asked: {get_response_error(response)}"
            body = extract_response_body(response)
            entries = parse_webdav_propfind_response(body) if body else []
            if not entries:
                return path, "left out of the listing, and Nextcloud's answer could not be read"
            if is_unreadable(entries[0]):
                return path, "its storage is not available"
            if entries[0].get("file_id") != str(record.external_record_id):
                return path, ""
            return path, "left out of the listing, but Nextcloud still has it"
        except Exception as e:
            return "", f"left out of the listing, and Nextcloud could not be asked: {str(e) or type(e).__name__}"

    async def run_sync(self) -> None:
        """
        Smart Sync: Automatically decides between Full vs. Incremental sync based on cursor state.
        - No cursor, or the last full listing is over FULL_SYNC_INTERVAL_MS old: full sync
        - Otherwise: incremental sync using the Activity API

        Raises whenever the run could not finish, so it is reported as failed.
        """
        try:
            self.logger.info("🔍 [Smart Sync] Starting Nextcloud sync...")
            if self.app_password_rejected:
                await self._app_password_rejected()

            # Load filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "nextcloud", self.connector_id, self.logger
            )

            # Cache date filters once at sync start for performance
            self._cached_date_filters = self._get_date_filters()

            if self.data_source is None:
                self.logger.warning("Data source not initialized, attempting to initialize...")
                if not await self.init() or self.data_source is None:
                    raise NextcloudSyncError(
                        "Could not connect to Nextcloud. Check the server URL, user name and app password."
                    )

            if not self.current_user_id:
                self.current_user_id, self.current_user_email = await self._read_current_user(self.data_source)
            if not self.current_user_id:
                raise NextcloudSyncError(
                    "Could not read the signed-in Nextcloud user, so nothing was synced; the next sync tries again."
                )

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
            if cursor_data and cursor_data.get('cursor') and not full_sync_due(cursor_data):
                self.logger.info(f"✅ [Smart Sync] Found existing cursor: {cursor_data.get('cursor')}")
                self.logger.info("🚀 [Smart Sync] Switching to INCREMENTAL SYNC.")
                await self._run_incremental_sync_internal()
                return

            self.logger.info("⚪ [Smart Sync] No cursor, or the daily full listing is due. Starting FULL SYNC.")
            await self._run_full_sync_internal()

        except Exception as ex:
            self.logger.error(f"Error in Nextcloud Smart Sync: {ex}", exc_info=True)
            if not isinstance(ex, ALREADY_NOTIFIED_ERRORS):
                await self.notify(
                    type=NotificationType.CONNECTOR_SYNC_ERROR,
                    severity=NotificationSeverity.ERROR,
                    title=f"{self.display_name}: Nextcloud sync failed",
                    message=f"{self.display_name}: {redact_urls_in_text(str(ex) or type(ex).__name__)}",
                )
            raise

    async def _link_creator_to_app(self) -> None:
        """Give the app's only user edge to the person who created this connector, and take
        it from anyone else.

        Never to the email in the Nextcloud profile: whoever owns that address in PipesHub
        would reach every record this personal connector syncs. Earlier versions linked that
        user, and one still running beside this one during a rolling upgrade can link them
        again after the startup migration removed the edge, so every full sync removes it.
        """
        if not self.creator_email:
            await self._load_creator_email()
        if not self.creator_email:
            raise NextcloudSyncError(
                "Could not look up the person who created this connector, so nothing was synced; "
                "the next sync tries again."
            )
        # The same edge ensure_creator_user_app_relation writes, but a failed write fails the run.
        await self.data_entities_processor.on_new_app_users([AppUser(
            app_name=Connectors.NEXTCLOUD,
            connector_id=self.connector_id,
            source_user_id=f"creator_{self.created_by or self.creator_email}",
            email=self.creator_email,
            full_name=self.creator_email,
            org_id=self.data_entities_processor.org_id,
            is_active=True,
        )])
        linked = await self.data_entities_processor.get_all_app_users(self.connector_id, raise_on_error=True)
        others = [u for u in linked if (u.email or "").lower() != self.creator_email.lower()]
        if others:
            self.logger.warning(
                f"Removing {len(others)} user(s) other than the connector's creator from this personal connector"
            )
            await self.data_entities_processor.withdraw_app_user_access(self.connector_id, others)

    async def _newest_activity_id(self) -> str | None:
        """The newest activity id, "0" when the feed is empty, None when it can't be read."""
        try:
            response = await self.data_source.get_activities(
                activity_filter=ACTIVITY_STREAM, limit=1, sort="desc"
            )
        except Exception as e:
            self.logger.warning(f"⚠️ [Full Sync] Could not read the newest activity: {e}")
            return None
        status = getattr(response, "status", None)
        if status in (HttpStatusCode.NO_CONTENT.value, HTTP_NOT_MODIFIED):
            return "0"
        await self._raise_if_app_password_rejected(response)
        if status == HttpStatusCode.NOT_FOUND.value:
            await self._note_activity_app_missing()
            return None
        if not is_response_successful(response):
            self.logger.warning(f"⚠️ [Full Sync] Could not read the newest activity: {get_response_error(response)}")
            return None
        activities = self._parse_activity_response(response)
        return str(activities[0].get('activity_id') or "0") if activities else "0"

    async def _note_activity_app_missing(self) -> None:
        self.logger.error("❌ The Nextcloud Activity app answered 404: incremental sync is unavailable")
        if self._activity_app_missing_noted:
            return
        self._activity_app_missing_noted = True
        await self.notify(
            type=NotificationType.CONNECTOR_SYNC_ERROR,
            severity=NotificationSeverity.WARNING,
            title=f"{self.display_name}: Nextcloud Activity app missing",
            message=f"{self.display_name}: {ACTIVITY_APP_MISSING_MESSAGE}",
        )

    async def _run_full_sync_internal(self) -> None:
        """List the whole drive, save what changed, remove what is gone, then anchor the cursor.

        Raises when any of that could not be done in full, so the runner unlinks nothing
        and the next sync lists the drive again.
        """
        try:
            if not self.current_user_id:
                raise NextcloudSyncError("The Nextcloud user is not known")

            await self._link_creator_to_app()

            # Create a record group for the personal drive
            record_group = RecordGroup(
                name=f"{self.current_user_id}'s Files",
                org_id=self.data_entities_processor.org_id,
                description="Personal Nextcloud Folder",
                external_group_id=self.current_user_id,
                connector_name=Connectors.NEXTCLOUD,
                connector_id=self.connector_id,
                group_type=RecordGroupType.DRIVE,
                inherit_permissions=True,
            )

            await self.data_entities_processor.on_new_record_groups([(record_group, [])])

            sync_point_key = "activity_cursor"
            checkpoint = await self.activity_sync_point.read_sync_point(sync_point_key) or {}
            stored_resave = checkpoint.get("full_sync_resave")
            before = sorted(str(i) for i in stored_resave) if isinstance(stored_resave, list) else []
            resave = set(before)
            listed: set[str] = set()
            # Only until one full listing finishes after the sync points were cleared (first sync,
            # a full sync from the UI or a filter change, an upgrade): it writes every record again.
            rewrite_unchanged = not checkpoint.get("last_full_sync_ms")
            started_ms = get_epoch_timestamp_in_ms()

            # Read before the listing: what changes while it runs comes after this id, so the
            # incremental sync replays it rather than skipping it.
            anchor = await self._newest_activity_id()

            # Sync files for the current user only
            self.logger.info(f"Syncing files for user: {self.current_user_email}")
            unread: dict[str, str] = {}
            returned: set[str] = set()
            complete = await self._sync_user_files(
                self.current_user_id,
                self.current_user_email or self.current_user_id,
                self.current_user_id,
                resave,
                listed,
                rewrite_unchanged=rewrite_unchanged,
                unread=unread,
                returned=returned,
            )
            if sorted(resave) != before:
                await self.activity_sync_point.update_sync_point(
                    sync_point_key, {"full_sync_resave": sorted(resave)}
                )
            if not complete:
                # The cursor only covers changes made after it, so saving one now would
                # leave whatever this run missed unsynced until it next changes.
                raise NextcloudSyncError(
                    "The drive could not be read or saved in full. The activity cursor was not saved, "
                    "so the next sync runs a full sync again."
                )

            if not await self._remove_records_not_listed(listed, returned, unread):
                # A filtered-out file is still in Nextcloud, so no later activity would
                # bring it back; without a cursor the next sync is full and finishes this.
                raise NextcloudSyncError(
                    "Some records Nextcloud no longer lists could not be removed. The activity cursor "
                    "was not saved, so the next sync runs a full sync again."
                )

            # The store merges writes, so a count left from an earlier cursor is reset here.
            done: dict[str, object] = {"last_full_sync_ms": started_ms, "held_attempts": 0}
            if anchor is not None:
                done["cursor"] = anchor
                self.logger.info(f"⚓ [Full Sync] Anchored activity cursor to: {anchor}")
            else:
                self.logger.warning("⚠️ [Full Sync] No activity cursor; the next sync lists the drive again")
            await self.activity_sync_point.update_sync_point(sync_point_key, done)

            if unread:
                await self.notify(
                    type=NotificationType.CONNECTOR_SYNC_ERROR,
                    severity=NotificationSeverity.WARNING,
                    title=f"{self.display_name}: some Nextcloud folders could not be read",
                    message=f"{self.display_name}: {FOLDERS_UNREADABLE_MESSAGE}{describe_failures(unread)}",
                )
            self.logger.info("✅ [Full Sync] Nextcloud full sync completed successfully.")

        except Exception as ex:
            self.logger.error(f"❌ [Full Sync] Error in full sync: {ex}", exc_info=True)
            raise

    async def _run_incremental_sync_internal(self) -> None:
        """
        Internal method for incremental synchronization using Activity API.

        Reads pages of activity until the feed has nothing newer (or the time budget is
        spent), moving the cursor after each page that applied. Raises when a page could
        not be read or some of its changes are held for a retry.
        """
        try:
            self.logger.info("🔄 [Incremental Sync] Starting incremental sync using Activity API...")

            if not self.data_source or not self.current_user_id:
                raise NextcloudSyncError("The Nextcloud client or user is not initialized")

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
            sync_point_data = await self.activity_sync_point.read_sync_point(sync_point_key) or {}
            last_activity_id = sync_point_data.get('cursor')

            if not last_activity_id:
                self.logger.warning("⚠️ [Incremental Sync] No cursor found. Falling back to full sync.")
                await self._run_full_sync_internal()
                return

            queued, pending_paths = read_pending_deletes(sync_point_data)
            pending_deletes = await self._retry_pending_deletes(sync_point_key, queued, pending_paths)
            await self._retry_pending_changes(sync_point_key, sync_point_data, existing_group.external_group_id)

            deadline = time.monotonic() + INCREMENTAL_TIME_BUDGET_SECONDS
            while True:
                self.logger.info(f"📋 [Incremental Sync] Fetching activities since ID: {last_activity_id}")
                response = await self.data_source.get_activities(
                    activity_filter=ACTIVITY_STREAM,
                    since=int(last_activity_id),
                    limit=ACTIVITY_PAGE_SIZE,
                    sort="asc"  # Ascending order to process oldest first
                )

                # HTTP 304 Not Modified (or 204) means no new activities
                status_code = response.status
                if status_code in (HTTP_NOT_MODIFIED, HttpStatusCode.NO_CONTENT.value):
                    self.logger.info("✅ [Incremental Sync] No new activities. Database is up to date.")
                    return

                await self._raise_if_app_password_rejected(response)

                if status_code == HttpStatusCode.NOT_FOUND.value:
                    # Without the Activity app nothing tells us what changed: list the drive instead.
                    await self._note_activity_app_missing()
                    await self._run_full_sync_internal()
                    return

                if not is_response_successful(response):
                    # A full sync in its place can't see deletions and would move the cursor past them.
                    raise NextcloudSyncError(
                        f"Failed to fetch Nextcloud activities: {get_response_error(response)}. The cursor "
                        "is kept, so the next sync reads the same changes again."
                    )

                activities = self._parse_activity_response(response)
                if not activities:
                    self.logger.info("✅ [Incremental Sync] No new activities found. Database is up to date.")
                    return

                # Nextcloud groups related rows into one activity, so neither the count nor the
                # ids returned tell where the page ended or whether more follow; its headers do.
                raw_headers = getattr(response, "headers", None)
                headers = {str(k).lower(): str(v) for k, v in raw_headers.items()} if isinstance(raw_headers, dict) else {}
                sync_point_data = await self.activity_sync_point.read_sync_point(sync_point_key) or {}
                last_activity_id = await self._apply_activity_page(
                    activities, last_activity_id, sync_point_key, sync_point_data,
                    pending_deletes, pending_paths, existing_group.external_group_id,
                    page_end=headers.get("x-activity-last-given"),
                )
                pending_deletes, pending_paths = read_pending_deletes(
                    await self.activity_sync_point.read_sync_point(sync_point_key) or {}
                )
                if 'rel="next"' not in headers.get("link", ""):
                    return
                if time.monotonic() > deadline:
                    self.logger.info("[Incremental Sync] Time budget spent; the next sync reads on from here")
                    return

        except Exception as ex:
            self.logger.error(f"❌ [Incremental Sync] Error: {ex}", exc_info=True)
            # Don't fall back to full sync on every error - let the scheduler retry
            raise

    async def _apply_activity_page(
        self,
        activities: list[dict],
        last_activity_id: str,
        sync_point_key: str,
        sync_point_data: dict,
        pending_deletes: list[str],
        pending_paths: dict[str, str],
        record_group_id: str,
        page_end: str | None = None,
    ) -> str:
        """Apply one page of activity and move the cursor past it; returns the new cursor.

        ``page_end`` is the id of the last row the page covered (Nextcloud's X-Activity-Last-Given),
        which a grouped activity's own id can be short of.

        Raises NextcloudChangesHeldError, keeping the cursor, when some changes could not be applied
        and the page has been read fewer than MAX_HELD_ATTEMPTS times.
        """
        user_id = self.current_user_id
        user_email = self.current_user_email or user_id
        self.logger.info(f"📋 [Incremental Sync] Found {len(activities)} new activities to process")

        # Extract unique file paths that were modified
        modified_paths = set()
        deleted_file_ids = set()
        deleted_paths: dict[str, str] = {}
        # Folders saved with everything below them: Nextcloud logs one activity for a restored,
        # copied or newly shared folder and none for what it holds.
        with_contents: set[str] = set()
        share_changes: dict[str, str] = {}
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
            if object_type != 'files':
                continue
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
                        if activity_type in ('file_restored', 'file_created'):
                            with_contents.add(file_path)
                        self.logger.info(f"📝 Modification detected: {file_path} ({activity_type})")
            elif activity_type == 'shared':
                # Shared with the user, or the share removed: the activity reads the same either
                # way, so where the file is now (or that it is gone) is asked by its id.
                for file_id, file_path in targets:
                    if file_id:
                        share_changes[str(file_id)] = file_path or ""

        if page_end and str(page_end).isdigit() and int(page_end) > int(max_activity_id):
            max_activity_id = str(page_end)

        failures: dict[str, str] = {}
        failed_deletes: dict[str, str] = {}
        found_ids: set[str] = set()

        for file_id, file_path in share_changes.items():
            found, href = await self._find_in_nextcloud_by_id(file_id)
            if found is None:
                failures[f"share change of {file_path or 'file'} (ID {file_id})"] = "could not look it up in Nextcloud by ID"
            elif found:
                path = "/" + path_inside_user_home(href, user_id)
                modified_paths.add(path)
                if await self.data_entities_processor.get_record_by_external_id(self.connector_id, file_id) is None:
                    with_contents.add(path)
                self.logger.info(f"🤝 Share change: {path} (ID: {file_id}) is in the drive")
            else:
                deleted_file_ids.add(file_id)
                deleted_paths[file_id] = file_path
                self.logger.info(f"🤝 Share change: {file_path} (ID: {file_id}) is no longer in the drive")

        # Process deletions
        if deleted_file_ids:
            self.logger.info(f"🗑️  [Incremental Sync] Processing {len(deleted_file_ids)} deletions")
            failed_deletes = await self._process_deletions(deleted_file_ids)
            for file_id, reason in failed_deletes.items():
                failures[f"deletion of {deleted_paths.get(file_id) or 'file'} (ID {file_id})"] = reason

        # Process modifications and new files
        failed_paths: dict[str, str] = {}
        if modified_paths:
            self.logger.info(f"📝 [Incremental Sync] Processing {len(modified_paths)} modified/new files")
            failed_paths = await self._process_modified_files(
                list(modified_paths),
                user_id,
                user_email,
                record_group_id,
                found_ids,
                with_contents,
            )
            failures.update(failed_paths)

        parked: list[str] = []
        # The feed won't list these activities again once the cursor moves past them.
        if failures:
            held_attempts = int(sync_point_data.get("held_attempts") or 0) + 1
            if held_attempts < MAX_HELD_ATTEMPTS:
                await self.activity_sync_point.update_sync_point(
                    sync_point_key,
                    {"cursor": str(last_activity_id), "held_attempts": held_attempts,
                     **pending_delete_fields(pending_deletes, pending_paths)},
                )
                raise NextcloudChangesHeldError(
                    f"{len(failures)} change(s) could not be applied (attempt {held_attempts} of "
                    f"{MAX_HELD_ATTEMPTS}); the cursor stays at {last_activity_id} so the next sync reads "
                    f"them again: {describe_failures(failures)}"
                )
            self.logger.error(
                f"❌ [Incremental Sync] {len(failures)} change(s) still could not be applied after "
                f"{held_attempts} attempts; moving on so later changes are not held up. They are "
                f"retried at the start of every sync until they apply: {describe_failures(failures)}"
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
            # A changed file that failed would otherwise wait until it changes again.
            parked = sorted(set(failed_paths) | set(sync_point_data.get("pending_changes") or []))
            await self.notify(
                type=NotificationType.CONNECTOR_SYNC_ERROR,
                severity=NotificationSeverity.WARNING,
                title=f"{self.display_name}: some Nextcloud changes not applied",
                message=f"{self.display_name}: {CHANGES_PARKED_MESSAGE}",
            )

        # Update cursor to latest activity ID
        fields: dict[str, object] = {"cursor": str(max_activity_id), "held_attempts": 0,
                                     **pending_delete_fields(pending_deletes, pending_paths)}
        if parked:
            fields["pending_changes"] = parked
        await self.activity_sync_point.update_sync_point(sync_point_key, fields)

        self.logger.info(
            f"✅ [Incremental Sync] Completed. Processed {len(modified_paths)} modified files, "
            f"{len(deleted_file_ids)} deletions. Cursor updated to {max_activity_id}"
        )
        return str(max_activity_id)

    async def _retry_pending_changes(self, sync_point_key: str, checkpoint: dict, record_group_id: str) -> None:
        """Apply again the changed paths an earlier run gave up on; the ones still failing stay queued.

        A path Nextcloud answers 404 for is gone (its deletion or move is in the feed) and leaves the queue.
        """
        pending = sorted(str(p) for p in checkpoint.get("pending_changes") or [] if p)
        if not pending:
            return
        failures = await self._process_modified_files(
            pending, self.current_user_id, self.current_user_email or self.current_user_id, record_group_id
        )
        remaining = sorted(failures)
        if remaining != pending:
            await self.activity_sync_point.update_sync_point(sync_point_key, {"pending_changes": remaining})
        if remaining:
            self.logger.warning(
                f"⚠️ [Incremental Sync] {len(remaining)} earlier change(s) still could not be applied; "
                f"they are retried next sync: {describe_failures(failures)}"
            )

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

        Looked up by ID, so a file or folder that moved is still found. When the lookup
        can't be made, a stored record falls back to its stored path; a folder whose
        record is gone stays unknown, since a 404 at its old path can't tell a move from
        a deletion and its contents would be deleted on that alone.
        """
        try:
            found, current_path = await self._find_in_nextcloud_by_id(file_id)
            if found is False:
                return True, ""
            if found:
                reason = await self._restore_moved_folder(file_id, current_path)
                return (None, reason) if reason else (False, "")
            record = await self.data_entities_processor.get_record_by_external_id(self.connector_id, file_id)
            if record is None:
                children = await self.data_entities_processor.get_records_by_parent(
                    connector_id=self.connector_id, parent_external_record_id=file_id
                )
                if not children:
                    return True, ""
                return None, "could not look it up in Nextcloud by ID"
            # A 404 proves the file gone only at its full stored path. The graph returns None
            # when the path read fails, and a bare name for a file with a parent isn't that path.
            path = await self.data_entities_processor.get_record_path(record.id)
            if not path:
                return None, "could not read its stored path"
            if record.parent_external_record_id and "/" not in path.strip("/"):
                return None, "its stored path is incomplete"
            async with self.rate_limiter:
                response = await self.data_source.list_directory(
                    user_id=self.current_user_id, path=path, depth=0, properties=SYNC_PROPERTIES
                )
            present = None
            if getattr(response, "status", None) != HttpStatusCode.NOT_FOUND.value:
                if not is_response_successful(response):
                    return None, f"could not check Nextcloud: {get_response_error(response)}"
                body = extract_response_body(response)
                entries = parse_webdav_propfind_response(body) if body else []
                if not entries:
                    return None, "could not read Nextcloud's answer"
                if is_unreadable(entries[0]):
                    return None, "its storage is not available"
                present = entries[0].get("file_id") == file_id
            if present:
                return False, ""
            # Not at its stored path. For a folder with records still stored below it that
            # can't tell a move from a deletion, and the deletion would take its contents.
            children = await self.data_entities_processor.get_records_by_parent(
                connector_id=self.connector_id, parent_external_record_id=file_id
            )
            if children:
                return None, "not at its stored path, and it could not be looked up by ID"
            return True, ""
        except Exception as e:
            return None, f"could not check Nextcloud: {str(e) or type(e).__name__}"

    async def _find_in_nextcloud_by_id(self, file_id: str) -> tuple[bool | None, str]:
        """Whether the user's files (not the trash) hold ``file_id``, and its current href.

        (None, "") when the search fails.
        """
        try:
            async with self.rate_limiter:
                response = await self.data_source.get_file_by_internal_id(self.current_user_id, file_id)
            if getattr(response, "status", None) == HttpStatusCode.UNAUTHORIZED.value:
                self.app_password_rejected = True
            if not is_response_successful(response):
                self.logger.debug(f"Search by ID failed for {file_id}: {get_response_error(response)}")
                return None, ""
            body = extract_response_body(response)
            # An empty multistatus is a real miss; an answer that isn't one is not.
            if not body or not is_multistatus(body):
                return None, ""
            match = next((e for e in parse_webdav_propfind_response(body) if e.get("file_id") == file_id), None)
            return (True, match.get("path", "")) if match else (False, "")
        except Exception as e:
            self.logger.debug(f"Search by ID failed for {file_id}: {e}")
            return None, ""

    async def _restore_moved_folder(self, file_id: str, current_href: str) -> str:
        """Save a found folder whose record is gone, with what it holds, where it now is.

        Returns why that failed, or "" when there was nothing to do or it worked. Nextcloud
        logs no activity for what a moved folder holds, so the records left below it after a
        partial cascade would otherwise stay linked to a folder record that no longer exists.
        Runs even when the folder record exists: an earlier attempt may have saved it and
        then failed on a child, whose parent link a failed save has already removed.
        """
        children = await self.data_entities_processor.get_records_by_parent(
            connector_id=self.connector_id, parent_external_record_id=file_id
        )
        if not children:
            return ""
        path = "/" + path_inside_user_home(current_href, self.current_user_id)
        failures = await self._process_modified_files(
            [path], self.current_user_id, self.current_user_email, self.current_user_id, None, {path}
        )
        if failures:
            return f"could not save it at {path}: {describe_failures(failures)}"
        self.logger.info(f"Folder {file_id} is at {path} now; it and its contents were saved there")
        return ""

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
                            depth=0,  # Only fetch this item, not children
                            properties=SYNC_PROPERTIES,
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
                        try:
                            record_update = await self._process_nextcloud_entry(
                                entry=entry,
                                user_id=user_id,
                                user_email=user_email,
                                record_group_id=record_group_id,
                                user_root_path=user_root_path,
                                path_to_external_id=parent_lookup
                            )

                            if record_update is None and self._filtered_out(entry):
                                await self._remove_filtered_out(entry)
                            elif record_update:
                                # For incremental sync: send new records immediately, handle updates separately
                                if record_update.is_new and record_update.record:
                                    await self.data_entities_processor.on_new_records(
                                        [(record_update.record, record_update.new_permissions or [])],
                                    )
                                elif not await self._handle_record_updates(record_update):
                                    failed[path] = "the change could not be saved"
                        except Exception:
                            # A later entry below this one would be saved under a parent that was
                            # never stored; a save links a child only to a stored parent, and a
                            # retry sees an unchanged parent id, so it would stay detached.
                            path_to_external_id.pop(entry.get('path', '').rstrip('/'), None)
                            raise

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

    def _filtered_out(self, entry: dict) -> bool:
        return bool(entry.get('file_id')) and not entry.get('is_collection') and not self._should_include_file(entry)

    async def _remove_filtered_out(self, entry: dict) -> None:
        """A stored file that no longer passes the sync filters (renamed to an excluded type, say) leaves the index."""
        stored = await self.data_entities_processor.get_record_by_external_id(self.connector_id, str(entry['file_id']))
        if stored is not None:
            self.logger.info(f"Removing {stored.record_name}: it no longer passes the sync filters or its download was disabled")
            await self.data_entities_processor.on_record_deleted(record_id=stored.id)

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
        try:
            entries, unread = await self._list_tree(user_id, path.strip("/"))
        except FileNotFoundError:
            return None
        except NextcloudSyncError as e:
            return f"its contents could not be listed: {e}"
        if unread:
            return f"folders inside it could not be read: {describe_failures(unread)}"
        below = self._sort_entries_by_hierarchy(entries[1:])
        path_to_external_id.update(await self._build_path_to_external_id_map(below))
        if found_ids is not None:
            found_ids.update(str(e['file_id']) for e in below if e.get('file_id'))

        failed_entries: list[str] = []
        batch: list[tuple[FileRecord, list[Permission]]] = []
        skipped = SkippedEntries(failed_entries)
        async for record, permissions, update in self._process_nextcloud_items_generator(
            below, user_id, user_email, record_group_id, user_root_path, path_to_external_id, failed_entries,
        ):
            if record and skipped.contains(record.parent_external_record_id):
                failed_entries.append(str(update.external_record_id))
                continue
            if update.is_new and record:
                batch.append((record, permissions))
                if len(batch) >= self.batch_size:
                    await self.data_entities_processor.on_new_records(batch)
                    batch = []
            else:
                # Folders sort ahead of files, so flushing here stores any new folder above this
                # child first; a save links a child only to a parent that is already stored.
                if batch:
                    await self.data_entities_processor.on_new_records(batch)
                    batch = []
                # Saved even when unchanged: after a partial cascade the folder comes back as a new
                # record, and only a save links a child that was already stored under it again.
                if not await self._handle_record_updates(dataclasses.replace(update, is_updated=True)):
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
                        user_id=user_id, path=folder_path, depth=0, properties=SYNC_PROPERTIES
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

    async def _current_path(self, record: Record) -> str | None:
        """Where the record's file is now, relative to the user's home, or None when Nextcloud no longer has it.

        Looked up by file id, so a file with the same name elsewhere is never taken for it.
        When that lookup can't be made, the stored path is used only if it is complete.
        Raises when neither can tell.
        """
        found, href = await self._find_in_nextcloud_by_id(str(record.external_record_id))
        if found is not None:
            return path_inside_user_home(href, self.current_user_id) if found else None
        path = await self.data_entities_processor.get_record_path(record.id)
        if not path or (record.parent_external_record_id and "/" not in path.strip("/")):
            raise NextcloudSyncError(f"Could not tell where {record.record_name} is in Nextcloud")
        return path_inside_user_home(path, self.current_user_id)

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Stream a file's content for download using authenticated request."""
        if not self.data_source:
            raise connector_not_ready(self.display_name)
        if self.app_password_rejected:
            raise map_source_status(HttpStatusCode.UNAUTHORIZED.value, connector=self.display_name)

        try:
            file_record = await self.data_entities_processor.get_file_record_by_id(record.id)
        except GraphQueryError as e:
            # Not a 404: the file may well be there, the store just didn't answer.
            self.logger.error(f"Could not read the file record of {record.id}: {e}")
            raise to_stream_error(e, connector=self.display_name) from e
        if not file_record:
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

        try:
            relative_path = await self._current_path(file_record)
            if self.app_password_rejected:
                raise map_source_status(HttpStatusCode.UNAUTHORIZED.value, connector=self.display_name)
            if relative_path is None:
                raise HTTPException(
                    status_code=HttpStatusCode.NOT_FOUND.value,
                    detail="File not found or access denied"
                )

            response = await self.data_source.open_download(self.current_user_id, relative_path)
            if not HTTP_STATUS_OK <= response.status_code < HTTP_STATUS_MULTIPLE_CHOICES:
                await response.aclose()
                if response.status_code == HttpStatusCode.UNAUTHORIZED.value:
                    self.app_password_rejected = True
                self.logger.error(f"Failed to download file for record {record.id}: HTTP {response.status_code}")
                raise map_source_status(response.status_code, connector=self.display_name)

            # Read in chunks as the client takes them; an empty file is an empty stream.
            async def generate() -> AsyncGenerator[bytes, None]:
                try:
                    async for chunk in response.aiter_bytes():
                        yield chunk
                finally:
                    await response.aclose()

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
        """Check the app password and user id with a Depth 0 PROPFIND on the user's home.

        A missing Activity app is only a warning: sync then lists the whole drive each time.
        Raises ConnectorInitError with the reason when access fails.
        """
        if not self.data_source or not self.current_user_id:
            raise ConnectorInitError(
                "Could not connect to Nextcloud or read the signed-in user. Check the server URL, "
                "user name and app password."
            )
        try:
            response = await self.data_source.list_directory(
                user_id=self.current_user_id, path="", depth=0, properties=SYNC_PROPERTIES
            )
        except Exception as e:
            raise ConnectorInitError(f"Could not reach Nextcloud: {e}") from e
        await self._raise_if_app_password_rejected(response)
        if not is_response_successful(response):
            raise ConnectorInitError(
                f"Nextcloud did not let {self.current_user_id} list their files: {get_response_error(response)}"
            )
        home = parse_webdav_propfind_response(extract_response_body(response) or b"")
        if home and is_unreadable(home[0]):
            raise NextcloudNoFilesystemAccessError(NO_FILESYSTEM_ACCESS_MESSAGE)
        try:
            activity = await self.data_source.get_activities(activity_filter=ACTIVITY_STREAM, limit=1)
            if getattr(activity, "status", None) == HttpStatusCode.NOT_FOUND.value:
                self.logger.warning(f"Nextcloud connection test: {ACTIVITY_APP_MISSING_MESSAGE}")
        except Exception as e:
            self.logger.warning(f"Nextcloud connection test could not read the activity feed: {e}")
        self.logger.info("Nextcloud connection test successful.")
        return True

    def handle_webhook_notification(self, notification: Dict) -> None:
        """Handle webhook notifications (not supported by Nextcloud)."""
        self.logger.warning(
            "Webhook notifications are not natively supported by Nextcloud. "
            "Use scheduled sync instead."
        )

    def _get_date_filters(self) -> Tuple[Optional[datetime], Optional[datetime]]:
        """The modified date filter's (after, before) bounds; WebDAV has no creation date to filter on."""
        modified_after: Optional[datetime] = None
        modified_before: Optional[datetime] = None

        modified_date_filter = self.sync_filters.get(SyncFilterKey.MODIFIED)
        if modified_date_filter and not modified_date_filter.is_empty():
            after_iso, before_iso = modified_date_filter.get_datetime_iso()
            if after_iso:
                modified_after = datetime.fromisoformat(after_iso).replace(tzinfo=timezone.utc)
                self.logger.info(f"Applying modified date filter: after {modified_after}")
            if before_iso:
                modified_before = datetime.fromisoformat(before_iso).replace(tzinfo=timezone.utc)
                self.logger.info(f"Applying modified date filter: before {modified_before}")

        return modified_after, modified_before

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

        # Nextcloud refuses to send its content (403), and the owner chose that it not leave Nextcloud.
        if entry.get('download_disabled'):
            self.logger.debug(f"Skipping {entry.get('display_name')}: shared with download disabled")
            return False

        # Get date filters from cache (performance optimization)
        modified_after, modified_before = self._cached_date_filters

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

            if self.data_source is not None:
                await self.data_source.client.close()
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
                file_record = await self.data_entities_processor.get_file_record_by_id(record.id)
                # Records inherit from the app and carry no user grant to look up; the
                # drive is only ever read as the connecting user, whose email a reindex
                # does not need.
                if not self.current_user_id or not file_record:
                    self.logger.warning(
                        f"Not reindexing {record.id}: "
                        f"{'the Nextcloud user is not known' if file_record else 'it has no stored file record'}"
                    )
                    failed_count += 1
                    continue
                path = await self._current_path(file_record)
                if path is None:
                    self.logger.info(f"{record.record_name} is no longer in Nextcloud; not reindexed")
                    failed_count += 1
                    continue

                async with self.rate_limiter:
                    response = await self.data_source.list_directory(
                        user_id=self.current_user_id,
                        path=path,
                        depth=0,
                        properties=SYNC_PROPERTIES,
                    )

                entries = []
                if is_response_successful(response):
                    entries = parse_webdav_propfind_response(extract_response_body(response) or b"")
                if not entries:
                    self.logger.warning(
                        f"Not reindexing {record.id}: reading /{path} from Nextcloud failed: "
                        f"{get_response_error(response) if not is_response_successful(response) else 'its answer could not be read'}"
                    )
                    failed_count += 1
                    continue

                record_update = await self._process_nextcloud_entry(
                    entries[0],
                    self.current_user_id,
                    self.current_user_email,
                    file_record.external_record_group_id,
                    user_root_path=None,
                    path_to_external_id={},
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
