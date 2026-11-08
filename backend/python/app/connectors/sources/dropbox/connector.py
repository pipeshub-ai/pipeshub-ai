import asyncio
import mimetypes
import re
import uuid

# from datetime import datetime
from datetime import datetime, timezone
from logging import Logger
from typing import AsyncGenerator, Dict, List, NoReturn, Optional, Tuple, Union

from aiolimiter import AsyncLimiter
from dropbox.exceptions import ApiError

# Dropbox SDK specific types
from dropbox.files import (
    DeletedMetadata,
    FileMetadata,
    FolderMetadata,
)
from dropbox.team_log import EventCategory, TeamEvent
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

# Base connector and service imports
from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.base.connector.connector_service import (
    BaseConnector,
    oauth_app_not_linked_error,
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
from app.connectors.core.constants import CONNECTOR_EMAIL_IDENTITY_INFO
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
from app.connectors.core.constants import (
    IconPaths,
)
# App-specific Dropbox client imports
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
from app.connectors.sources.dropbox.access import DropboxGrants, member_is_direct
from app.connectors.sources.dropbox.common.apps import DropboxApp
from app.connectors.sources.dropbox.deletes import (
    DeleteOutcome,
    apply_deleted_entry,
    apply_out_of_scope_entry,
    apply_replaced_entry,
    item_still_exists,
    recheck_owed_deletes,
)
from app.connectors.sources.dropbox.errors import is_auth_error, is_cursor_reset, missing_scope
from app.connectors.sources.dropbox.owed import OwedDeletes
from app.connectors.sources.dropbox.parents import ParentResolver
from app.connectors.sources.dropbox.paths import repath_descendants
from app.connectors.sources.dropbox.reconcile import remove_unlisted
from app.connectors.sources.dropbox.sync_health import SyncHealth
from app.connectors.sources.dropbox.sync_notices import SyncNotices
from app.connectors.sources.dropbox.team_directory import read_team_members, withdrawn
from app.connectors.sources.dropbox.weblinks import WebLinks
from app.connectors.sources.dropbox.shared_folders import (
    SharedFolder,
    folder_grants,
    outside_namespace,
    parents_first,
    plan_shared_folders,
)
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate

# Model imports
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    User,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.dropbox.dropbox_ import (
    DropboxClient,
    DropboxResponse,
    DropboxTokenConfig,
)
from app.sources.client.dropbox.endpoint import dropbox_api_base_url
from app.sources.external.dropbox.dropbox_ import DropboxDataSource
from app.connectors.core.base.error.stream_errors import (
    connector_not_ready,
    not_downloadable,
    not_found_at_source,
    raise_for_stream_fetch,
    to_stream_error,
)
from app.utils.streaming import create_stream_record_response, stream_content

# from dropbox.team import GroupSelector



# How many members a delete's existence check asks: it runs only when the reporting
# member no longer sees the item, so it stays a handful of calls.
_MAX_MEMBERS_TO_ASK = 5
# Members tried in turn for a stream; each costs one temporary-link call on a user-facing request.
_MAX_STREAM_CANDIDATES = 25
# Sync point holding the nested shared folders the last complete run planned.
_NESTED_FOLDERS_KEY = generate_record_sync_point_key(RecordType.DRIVE.value, "nested_shared_folders", "team")

# Team-log sharing events after which an item's members are read again.
SHARING_CHANGE_EVENTS = frozenset({
    "shared_content_add_member",
    "shared_content_remove_member",
    "shared_content_relinquish_membership",
    "shared_content_add_invitees",
    "shared_content_remove_invitees",
    "shared_content_claim_invitation",
    "shared_content_change_member_role",
    "shared_content_unshare",
    "shared_folder_create",
    "shared_folder_nest",
    "shared_folder_transfer_ownership",
    "shared_folder_change_members_inheritance_policy",
})

class GroupAccessRemovalError(Exception):
    """A member removal or group deletion in the group event log that could not be saved.

    Dropbox lists an event once, so the handlers let this one through instead of
    logging it: the group is then queued, and what the event should have removed
    is removed on a later run.
    """

    def __init__(self, message: str, group_id: str, *, group_deleted: bool) -> None:
        super().__init__(message)
        self.group_id = group_id
        self.group_deleted = group_deleted


# Add these helper functions at the top of the file
def get_parent_path_from_path(path: str) -> Optional[str]:
    """Extracts the parent path from a file/folder path."""
    if not path or path == "/" or "/" not in path.lstrip("/"):
        return None  # Root directory has no parent path in this context
    parent_path = "/".join(path.strip("/").split("/")[:-1])
    return f"/{parent_path}" if parent_path else "/"

def get_file_extension(filename: str) -> Optional[str]:
    """Extracts the extension from a filename."""
    if "." in filename:
        parts = filename.split(".")
        if len(parts) > 1:
            return parts[-1].lower()
    return None

def get_mimetype_enum_for_dropbox(entry: Union[FileMetadata, FolderMetadata]) -> MimeTypes:
    """
    Determines the correct MimeTypes enum member for a Dropbox API entry.

    Args:
        entry: A FileMetadata or FolderMetadata object from the Dropbox SDK.

    Returns:
        The corresponding MimeTypes enum member.
    """
    # 1. Handle folders directly
    if isinstance(entry, FolderMetadata):
        return MimeTypes.FOLDER

    # 2. Handle files by guessing the type from the filename
    if isinstance(entry, FileMetadata):
        # The '.paper' extension is a special Dropbox file type. We can handle it explicitly
        # or let it fall through to the default binary type if not in the enum.
        if entry.name.endswith('.paper'):
             # Assuming .paper files are a form of web content.
             return MimeTypes.HTML

        # Use the mimetypes library to guess from the extension
        mime_type_str, _ = mimetypes.guess_type(entry.name)

        if mime_type_str:
            try:
                # 3. Attempt to convert the guessed string into our MimeTypes enum
                return MimeTypes(mime_type_str)
            except ValueError:
                # 4. If the guessed type is not in our enum (e.g., 'application/zip'),
                # fall back to the generic binary type.
                return MimeTypes.BIN

    # 5. Fallback for any unknown file type or if mimetypes fails
    return MimeTypes.BIN

# @dataclass
# class RecordUpdate:
#     """Track updates to a record"""
#     record: Optional[FileRecord]
#     is_new: bool
#     is_updated: bool
#     is_deleted: bool
#     metadata_changed: bool
#     content_changed: bool
#     permissions_changed: bool
#     old_permissions: Optional[List[Permission]] = None
#     new_permissions: Optional[List[Permission]] = None
#     external_record_id: Optional[str] = None

def _reset_time(error: object) -> datetime | None:
    """The timestamp of a GetTeamEventsContinueError.reset, as the SDK prints it, or None."""
    match = re.search(r"'reset', datetime\.datetime\(([\d, ]+)\)", str(error or ""))
    if not match:
        return None
    parts = [int(p) for p in match.group(1).split(",")]
    return datetime(*parts, tzinfo=timezone.utc)


def _next_cursor(page: object) -> str | None:
    """The cursor of a sharing list page; Dropbox leaves it unset on the last page."""
    cursor = getattr(page, "cursor", None)
    return cursor if isinstance(cursor, str) and cursor else None


@ConnectorBuilder("Dropbox")\
    .in_group("Cloud Storage")\
    .with_description("Sync files and folders from Dropbox")\
    .with_categories(["Storage"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Dropbox",
            authorize_url="https://www.dropbox.com/oauth2/authorize",
            token_url="https://api.dropboxapi.com/oauth2/token",
            redirect_uri="connectors/oauth/callback/Dropbox",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[
                    "account_info.read",
                    "files.content.read",
                    "files.metadata.read",
                    "file_requests.read",
                    "groups.read",
                    "members.read",
                    "sharing.read",
                    "sharing.write",
                    "team_data.member",
                    "team_data.team_space",
                    "team_info.read",
                    "events.read"
                ],
                agent=[]
            ),
            fields=[
                CommonFields.client_id("Dropbox App Console"),
                CommonFields.client_secret("Dropbox App Console")
            ],
            icon_path=IconPaths.connector_icon(Connectors.DROPBOX.value),
            app_group="Cloud Storage",
            app_description="OAuth application for accessing Dropbox API and team collaboration features",
            app_categories=["Storage"],
            token_access_type="offline"
        )
    ])\
    .with_info(CONNECTOR_EMAIL_IDENTITY_INFO)\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.DROPBOX.value))
        .add_documentation_link(DocumentationLink(
            "Dropbox App Setup",
            "https://developers.dropbox.com/oauth-guide",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/dropbox/dropbox_teams',
            'pipeshub'
        ))
        .add_filter_field(CommonFields.modified_date_filter("Filter files and folders by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter files and folders by creation date."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .add_filter_field(CommonFields.file_extension_filter())
        .add_filter_field(FilterField(
            name="shared",
            display_name="Index Shared Items",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of shared items",
            default_value=True
        ))
        .with_webhook_config(True, ["file.added", "file.modified", "file.deleted"])
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .add_sync_custom_field(CommonFields.batch_size_field())
        .with_sync_support(True)
        .with_agent_support(True)
    )\
    .build_decorator()
class DropboxConnector(BaseConnector):
    """
    Connector for synchronizing data from a Dropbox account.
    """

    current_user_id: Optional[str] = None

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
            DropboxApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )

        self.connector_name = Connectors.DROPBOX
        self.connector_id = connector_id



        # Initialize sync point for tracking record changes
        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider
            )
        # Initialize sync points
        self.dropbox_cursor_sync_point = _create_sync_point(SyncDataPointType.RECORDS)
        self.user_sync_point = _create_sync_point(SyncDataPointType.USERS)
        self.user_group_sync_point = _create_sync_point(SyncDataPointType.GROUPS)

        self.data_source: Optional[DropboxDataSource] = None
        self.batch_size = 100
        self.max_concurrent_batches = 1 # set to 1 for now to avoid write write conflicts for small number of records
        self.rate_limiter = AsyncLimiter(50, 1)  # 50 requests per second
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()
        # This run's shared folders, so a sharing event is re-read as the folder's syncer.
        self._shared_folder_plan: dict[str, SharedFolder] = {}
        self._health = SyncHealth()
        self._owed = OwedDeletes(self.dropbox_cursor_sync_point)
        self._web_links: WebLinks | None = None
        # Emails of the team's members, and of the people outside the team that this run granted something to.
        self._team_emails: set[str] = set()
        self._external_emails: set[str] = set()
        # Folders whose stored path changed in the page being written: (external id, old path, new path).
        self._moved_folders: list[tuple[str, str, str]] = []
        # Shared and team folders with no live record stored: listed from the start, not from a cursor.
        self._fresh_folders: set[str] = set()

    async def init(self) -> bool:
        """Initializes the Dropbox client using credentials from the config service."""
        config = await self.config_service.get_config(
            f"/services/connectors/{self.connector_id}/config"
        )
        if not config:
            self.logger.error("Dropbox access token not found in configuration.")
            return False

        credentials_config = config.get("credentials")
        access_token = credentials_config.get("access_token")
        refresh_token = credentials_config.get("refresh_token")
        is_team = credentials_config.get("isTeam", True)

        auth_config = config.get("auth") or {}
        oauth_config_id = auth_config.get("oauthConfigId")

        if not oauth_config_id:
            self.logger.error(
                "Dropbox has no oauthConfigId in its auth config; it reads its "
                "client ID and secret only from a linked OAuth app."
            )
            raise oauth_app_not_linked_error()

        oauth_config = await self._fetch_oauth_config_by_id(
            oauth_config_id=oauth_config_id,
            connector_type=Connectors.DROPBOX.value,
            auth_config=auth_config,
        )

        if not oauth_config:
            self.logger.error(f"Linked OAuth app not found for Dropbox connector {self.connector_id}.")
            return False

        # Use credentials from OAuth config
        oauth_config_data = oauth_config.get("config", {})
        app_key = oauth_config_data.get("clientId") or oauth_config_data.get("client_id")
        app_secret = oauth_config_data.get("clientSecret") or oauth_config_data.get("client_secret")
        self.logger.info(f"Using linked OAuth app for Dropbox connector {self.connector_id}")

        try:
            config = DropboxTokenConfig(
                token=access_token,
                refresh_token=refresh_token,
                app_key=app_key,
                app_secret=app_secret
            )
            client = await DropboxClient.build_with_config(config, is_team=is_team)
            self.data_source = DropboxDataSource(client)
            if dropbox_api_base_url():
                self.logger.warning(
                    "Dropbox calls for this connector go to %s (DROPBOX_API_BASE_URL), not to Dropbox: "
                    "only a test deployment should set it.", dropbox_api_base_url()
                )
            self.logger.info("Dropbox client initialized successfully.")
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize Dropbox client: {e}", exc_info=True)
            return False

    async def _process_dropbox_entry(
        self, entry: Union[FileMetadata, FolderMetadata, DeletedMetadata],
         user_id: str, user_email: str,
          record_group_id: str,
          is_person_folder: bool,
          modified_after: Optional[datetime] = None,
          modified_before: Optional[datetime] = None,
          created_after: Optional[datetime] = None,
          created_before: datetime | None = None,
          *,
          parents: ParentResolver | None = None,
          force_members: bool = False,
    ) -> Optional[RecordUpdate]:
        """
        Turn one listed entry into the record to write, and say what changed.

        ``new_permissions`` is the file's own members as read from Dropbox, ``[]`` when Dropbox says the file has
        no explicit members, or None when they could not be read (the stored ones are then kept). A delete is
        applied here and gives None.
        """
        team_folder_id = record_group_id if not is_person_folder else None
        try:
            # A delete has no id, dates or extension to filter on.
            if isinstance(entry, DeletedMetadata):
                await self._apply_delete(entry.path_lower, user_id, record_group_id)
                if parents is not None:
                    parents.note_gone(entry.path_lower)
                return None

            if not (self._pass_date_filters(entry, modified_after, modified_before, created_after, created_before)
                    and self._pass_extension_filter(entry)):
                self.logger.debug(f"Skipping item {entry.name} (ID: {entry.id}): outside the sync filters.")
                self._note_delete_outcome(await apply_out_of_scope_entry(
                    self.data_entities_processor, self.connector_id, entry.id, self.logger,
                ))
                return None

            existing_record = await self.data_entities_processor.get_record_by_external_id(
                self.connector_id, entry.id
            )
            is_file = isinstance(entry, FileMetadata)
            if existing_record is None:
                # Another item stored at this path was replaced (deleted, and this one made in its place).
                outcome = await apply_replaced_entry(
                    self.data_entities_processor, self.connector_id, entry.path_lower, entry.id, record_group_id,
                    lambda record: self._team_item_still_exists(record, user_id), self.logger,
                )
                self._note_delete_outcome(outcome)

            if parents is not None:
                parent_external_record_id = await parents.parent_id(entry.path_lower)
            else:
                parent_external_record_id = await self._ask_parent_id(
                    get_parent_path_from_path(entry.path_lower), user_id, team_folder_id
                )

            if is_file:
                timestamp_ms = int(entry.server_modified.timestamp() * 1000)
                created_ms = int((getattr(entry, "client_modified", None) or entry.server_modified).timestamp() * 1000)
            elif existing_record is not None:
                # Folders carry no dates; keep the stored ones instead of moving them on every sync.
                timestamp_ms = existing_record.source_updated_at or existing_record.updated_at
                created_ms = existing_record.source_created_at or existing_record.created_at
            else:
                timestamp_ms = created_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

            weburl = await self._links().link_for(
                entry, stored=existing_record.weburl if existing_record else None,
                team_member_id=user_id, team_folder_id=team_folder_id,
            )

            file_record = FileRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=entry.name,
                record_type=RecordType.FILE,
                record_group_type=RecordGroupType.DRIVE.value,
                external_record_group_id=record_group_id,
                external_record_id=entry.id,
                external_revision_id=entry.rev if is_file else None,
                # The processor sets the version: it moves on a content change only.
                version=0,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                created_at=created_ms,
                updated_at=timestamp_ms,
                source_created_at=created_ms,
                source_updated_at=timestamp_ms,
                weburl=weburl,
                parent_external_record_id=parent_external_record_id,
                # Lets the processor stand in a placeholder for a parent listed after its child.
                parent_record_type=RecordType.FILE if parent_external_record_id else None,
                size_in_bytes=entry.size if is_file else 0,
                is_file=is_file,
                extension=get_file_extension(entry.name) if is_file else None,
                path=entry.path_lower,
                mime_type=get_mimetype_enum_for_dropbox(entry),
                sha256_hash=entry.content_hash if is_file and hasattr(entry, 'content_hash') else None,
            )

            # A file keeps only the members invited on it; everything else is inherited
            # from its folder, its shared folder's record group, or the member's personal
            # group. Folders inside a namespace have no members of their own, and a
            # shared folder is a record group, not a record.
            new_permissions: list[Permission] | None = []
            if is_file:
                explicit = getattr(entry, "has_explicit_shared_members", None)
                if explicit is False and not force_members:
                    # Dropbox says the file has no members of its own: nothing to read.
                    new_permissions = DropboxGrants([], covered_emails=set())
                else:
                    try:
                        new_permissions = await self._convert_dropbox_permissions_to_permissions(
                            file_or_folder_id=entry.id, is_file=True, team_member_id=user_id,
                        )
                    except Exception as perm_ex:
                        self.logger.warning(f"Could not fetch permissions for {entry.name}: {perm_ex}")
                        new_permissions = None
                    if new_permissions is None:
                        self._health.skipped(f"members of {entry.path_lower}", "could not be read; stored access kept")
                if new_permissions:
                    self._note_grant_emails(new_permissions)
            file_record.is_shared = bool(getattr(entry, "sharing_info", None)) or bool(new_permissions)
            file_record.inherit_permissions = True
            file_record.rewrite_permissions = new_permissions is not None

            if existing_record is not None and not is_file:
                stored_file = await self.data_entities_processor.get_file_record_by_id(existing_record.id)
                old_path = getattr(stored_file, "path", None)
                if old_path and old_path != entry.path_lower:
                    self._moved_folders.append((entry.id, old_path, entry.path_lower))

            metadata_changed = content_changed = False
            if existing_record is not None:
                metadata_changed = (
                    existing_record.record_name != entry.name
                    or existing_record.external_record_group_id != record_group_id
                    or existing_record.parent_external_record_id != parent_external_record_id
                    or getattr(existing_record, "path", entry.path_lower) != entry.path_lower
                )
                content_changed = is_file and existing_record.external_revision_id != entry.rev

            return RecordUpdate(
                record=file_record,
                is_new=existing_record is None,
                is_updated=existing_record is not None,
                is_deleted=False,
                metadata_changed=metadata_changed,
                content_changed=content_changed,
                permissions_changed=new_permissions is not None,
                old_permissions=[],
                new_permissions=new_permissions,
                external_record_id=entry.id
            )
        except Exception as ex:
            self.logger.error(f"Error processing Dropbox entry {getattr(entry, 'id', entry.path_lower)}: {ex}", exc_info=True)
            self._health.skipped(f"entry {getattr(entry, 'path_lower', '?')}", ex)
            return None

    async def _apply_delete(self, path_lower: str, member_id: str | None, record_group_id: str | None) -> None:
        outcome = await apply_deleted_entry(
            self.data_entities_processor, self.connector_id, path_lower,
            lambda record: self._team_item_still_exists(record, member_id), self.logger,
            record_group_id=record_group_id,
        )
        self._note_delete_outcome(outcome)

    def _note_delete_outcome(self, outcome: DeleteOutcome) -> None:
        """A delete Dropbox could not confirm is owed: the cursor moves on, and Dropbox lists the change only once."""
        if outcome.unsure_external_ids:
            self._owed.add(outcome.unsure_external_ids)
        if outcome.failed:
            self._health.skipped("a delete", "could not be written; it is tried again on the next run")

    def _note_grant_emails(self, permissions: list[Permission]) -> None:
        for permission in permissions:
            email = (permission.email or "").lower()
            if permission.entity_type == EntityType.USER and email and email not in self._team_emails:
                self._external_emails.add(email)

    def _links(self) -> WebLinks:
        if self._web_links is None or self._web_links.data_source is not self.data_source:
            self._web_links = WebLinks(self.data_source, self.logger)
        return self._web_links

    async def _stored_id_at(self, path_lower: str, record_group_id: str) -> str | None:
        for record in await self.data_entities_processor.get_records_by_file_path(self.connector_id, path_lower):
            if record.external_record_group_id == record_group_id:
                return record.external_record_id
        return None

    async def _ask_parent_id(self, parent_path: str | None, member_id: str | None,
                             team_folder_id: str | None) -> str | None:
        if not parent_path or parent_path == "/":
            return None
        metadata = await self.data_source.files_get_metadata(
            parent_path, team_member_id=member_id, team_folder_id=team_folder_id,
        )
        found = item_still_exists(metadata)
        if found is None:
            # Unknown is not "no parent": the entry would be written at the root and Dropbox lists it once.
            raise RuntimeError(f"could not read the parent folder {parent_path}: {metadata.error}")
        return metadata.data.id if found else None

    async def _team_item_still_exists(self, record: Record, member_id: str | None) -> bool | None:
        """Whether a deleted path's record still resolves by id for anyone who can open it.

        A member who leaves a shared folder sees a delete there while the owner
        keeps the item, so the id is asked as the reporting member first, then as
        each member the graph says can open the record or a folder above it, and
        the member whose sync wrote the record. Stops at the first that resolves.
        """
        members = await self._members_to_ask(record, member_id)
        if not members:
            # Nobody to ask is no answer: a delete is applied only on Dropbox's word.
            return None
        unknown = False
        # An id outside the member's home resolves only under its namespace's root: on the team member root
        # layout a team folder item asked from the member's default root is not_found even for its owner.
        group = record.external_record_group_id
        namespace = group if group and not group.startswith("dbmid:") else None
        for member in members:
            answer = item_still_exists(await self.data_source.files_get_metadata(
                path=record.external_record_id, team_member_id=member, team_folder_id=namespace,
            ))
            if answer:
                return True
            unknown = unknown or answer is None
        return None if unknown else False

    async def _members_to_ask(self, record: Record, member_id: str | None) -> list[str]:
        """The reporting member, the members who can open the record, and the member whose sync wrote it."""
        members = [member_id] if member_id else []
        for member in await self._members_with_access(record, _MAX_MEMBERS_TO_ASK):
            if member not in members:
                members.append(member)
        writer = record.external_record_group_id
        if writer and writer.startswith("dbmid:") and writer not in members:
            members.append(writer)
        return members

    async def _members_with_access(self, record: Record, limit: int) -> list[str]:
        """Team member ids the graph says can open a record, to act as when reading it.

        Users granted on the record or a folder above it come first. Only when there
        is none, the members of the groups granted there are listed from Dropbox: a
        record shared through a group alone names no user.
        """
        members: list[str] = []
        for user in await self.data_entities_processor.get_users_with_permission_to_node(
            record.id, CollectionNames.RECORDS.value
        ):
            app_user = await self.data_entities_processor.get_app_user_by_email(user.email, self.connector_id) if user.email else None
            if app_user and app_user.is_active is not False and app_user.source_user_id not in members:
                members.append(app_user.source_user_id)
                if len(members) >= limit:
                    return members
        if members:
            return members
        for group in await self.data_entities_processor.get_groups_with_access_to_record(record):
            try:
                group_members = await self._fetch_group_members(group.source_user_group_id, group.name)
            except Exception as e:
                self.logger.warning(f"Could not list the members of group {group.name}: {e}")
                continue
            for group_member in group_members:
                profile = getattr(group_member, "profile", None)
                member_id = getattr(profile, "team_member_id", None)
                status = getattr(profile, "status", None)
                if not member_id or member_id in members or (status is not None and not status.is_active()):
                    continue
                members.append(member_id)
                if len(members) >= limit:
                    return members
        return members

    async def _process_dropbox_items_generator(
        self, entries: List[Union[FileMetadata, FolderMetadata, DeletedMetadata]], user_id: str, user_email: str, record_group_id: str, is_person_folder: bool,
        modified_after: Optional[datetime] = None,
        modified_before: Optional[datetime] = None,
        created_after: Optional[datetime] = None,
        created_before: datetime | None = None,
        *,
        shared_folder_ids: frozenset[str] = frozenset(),
        parents: ParentResolver | None = None,
    ) -> AsyncGenerator[Tuple[Optional[FileRecord], List[Permission], RecordUpdate], None]:
        """
        Process Dropbox entries and yield records with their permissions.
        This allows non-blocking processing of large datasets.

        Entries of another shared folder (``shared_folder_ids``) are left out: that
        folder is a record group and its namespace is listed on its own.
        """
        namespace_id = None if is_person_folder else record_group_id
        if parents is not None:
            # A page may list a child before its folder.
            for entry in entries:
                if isinstance(entry, FolderMetadata) and not outside_namespace(entry, namespace_id, shared_folder_ids):
                    parents.note_folder(entry.path_lower, entry.id)
        for entry in entries:
            try:
                if outside_namespace(entry, namespace_id, shared_folder_ids):
                    await self._drop_mount_record(entry, record_group_id)
                    continue
                record_update = await self._process_dropbox_entry(
                    entry, user_id, user_email, record_group_id, is_person_folder,
                    modified_after=modified_after,
                    modified_before=modified_before,
                    created_after=created_after,
                    created_before=created_before,
                    parents=parents,
                )
                if record_update and record_update.record:
                    files_disabled = not self.indexing_filters.is_enabled(IndexingFilterKey.FILES, default=True)
                    shared_disabled = record_update.record.is_shared and not self.indexing_filters.is_enabled(IndexingFilterKey.SHARED, default=True)
                    if files_disabled or shared_disabled:
                        record_update.record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

                    yield (record_update.record, record_update.new_permissions or [], record_update)
                await asyncio.sleep(0)
            except Exception as e:
                self.logger.error(f"Error processing item in generator: {e}", exc_info=True)
                self._health.skipped(f"entry {getattr(entry, 'path_lower', '?')}", e)
                continue

    def _pass_date_filters(
        self,
        entry: Union[FileMetadata, FolderMetadata, DeletedMetadata],
        modified_after: Optional[datetime] = None,
        modified_before: Optional[datetime] = None,
        created_after: Optional[datetime] = None,
        created_before: Optional[datetime] = None
    ) -> bool:
        """
        Returns True if entry PASSES filters (should be kept)

        Note: Dropbox only provides `server_modified` (modification date) for files.
        For `client_modified` (creation/upload date), we use it as the "created" date.
        Folders don't have date metadata, so they are never filtered out.

        Args:
            entry: The Dropbox file/folder metadata
            modified_after: Skip files modified before this date
            modified_before: Skip files modified after this date
            created_after: Skip files created before this date
            created_before: Skip files created after this date

        Returns:
            False if the entry should be skipped, True otherwise
        """
        # Folders don't have date metadata - never filter them out
        if not isinstance(entry, FileMetadata):
            return True

        # No filters applied
        if not any([modified_after, modified_before, created_after, created_before]):
            return True

        # Get the dates from the entry
        # server_modified = last time the file was modified on Dropbox
        # client_modified = modification time set by the desktop client when file was added
        server_modified = entry.server_modified
        client_modified = getattr(entry, 'client_modified', None)

        # Ensure timezone awareness for comparison
        if server_modified and server_modified.tzinfo is None:
            server_modified = server_modified.replace(tzinfo=timezone.utc)
        if client_modified and client_modified.tzinfo is None:
            client_modified = client_modified.replace(tzinfo=timezone.utc)

        # Apply modified date filters (using server_modified)
        if server_modified:
            if modified_after and server_modified < modified_after:
                self.logger.debug(f"Skipping {entry.name}: modified {server_modified} before cutoff {modified_after}")
                return False
            if modified_before and server_modified > modified_before:
                self.logger.debug(f"Skipping {entry.name}: modified {server_modified} after cutoff {modified_before}")
                return False

        # Apply created date filters (using client_modified as proxy for creation date)
        # If client_modified is not available, fall back to server_modified
        created_date = client_modified or server_modified
        if created_date:
            if created_after and created_date < created_after:
                self.logger.debug(f"Skipping {entry.name}: created {created_date} before cutoff {created_after}")
                return False
            if created_before and created_date > created_before:
                self.logger.debug(f"Skipping {entry.name}: created {created_date} after cutoff {created_before}")
                return False

        return True

    def _pass_extension_filter(self, entry: Union[FileMetadata, FolderMetadata, DeletedMetadata]) -> bool:
        """
        Checks if the Dropbox entry passes the configured file extensions filter.

        For MULTISELECT filters:
        - Operator IN: Only allow files with extensions in the selected list
        - Operator NOT_IN: Allow files with extensions NOT in the selected list

        Folders and deleted items always pass this filter to maintain directory structure.

        Args:
            entry: The Dropbox file/folder/deleted metadata

        Returns:
            True if the entry passes the filter (should be kept), False otherwise
        """
        # 1. ALWAYS Allow Folders and Deleted items
        # We must sync folders regardless of extension to ensure the directory structure
        # exists for any files that might be inside them.
        # Deleted items should pass through so deletions are processed.
        if not isinstance(entry, FileMetadata):
            return True

        # 2. Get the extensions filter
        extensions_filter = self.sync_filters.get(SyncFilterKey.FILE_EXTENSIONS)

        # If no filter configured or filter is empty, allow all files
        if extensions_filter is None or extensions_filter.is_empty():
            return True

        # 3. Get the file extension from the entry name
        # The extension is stored without the dot (e.g., "pdf", "docx")
        file_extension = None
        if entry.name and "." in entry.name:
            file_extension = entry.name.rsplit(".", 1)[-1].lower()

        # 4. Handle files without extensions
        if file_extension is None:
            operator = extensions_filter.get_operator()
            operator_str = operator.value if hasattr(operator, 'value') else str(operator)
            return operator_str == FilterOperator.NOT_IN

        # 5. Get the list of extensions from the filter value
        allowed_extensions = extensions_filter.value
        if not isinstance(allowed_extensions, list):
            return True  # Invalid filter value, allow the file

        # Normalize extensions (lowercase, without dots)
        normalized_extensions = [ext.lower().lstrip(".") for ext in allowed_extensions]

        # 6. Apply the filter based on operator
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

    async def _convert_dropbox_permissions_to_permissions(
        self,
        file_or_folder_id: str,
        is_file: bool,
        team_member_id: Optional[str] = None,
        shared_folder_id: Optional[str] = None
    ) -> list[Permission] | None:
        """
        Convert Dropbox permissions to our Permission model; None when they could not be read.
        Handles both user and group permissions for files and folders.

        Args:
            file_or_folder_id: The Dropbox file or folder ID
            is_file: True if this is a file, False if it's a folder
            team_member_id: The team member ID to use for the API call
            shared_folder_id: For shared folders, the shared folder ID

        Returns:
            List of Permission objects
        """
        permissions = []
        covered_emails: set[str] = set()

        try:
            # Fetch members based on type
            if is_file:
                members_result = await self.data_source.sharing_list_file_members(
                    file=file_or_folder_id,
                    include_inherited=True,
                    team_member_id=team_member_id
                )
            else:
                # For folders, only fetch if it's a shared folder
                if not shared_folder_id:
                    # Not a shared folder, no permissions to fetch
                    return []
                members_result = await self.data_source.sharing_list_folder_members(
                    shared_folder_id=shared_folder_id,
                    team_member_id=team_member_id
                )

            if not members_result.success:
                self.logger.warning(f"Could not fetch permissions for {file_or_folder_id}: {members_result.error}")
                return None
            if is_file:
                # sharing/list_file_members returns at most 100 members a page (sharing.stone); the rest follow.
                cursor = _next_cursor(members_result.data)
                while cursor:
                    page = await self.data_source.sharing_list_file_members_continue(
                        cursor=cursor, team_member_id=team_member_id,
                    )
                    if not page.success:
                        self.logger.warning(f"Could not read every member of {file_or_folder_id}: {page.error}")
                        return None
                    members_result.data.users.extend(page.data.users)
                    members_result.data.groups.extend(page.data.groups)
                    members_result.data.invitees.extend(page.data.invitees)
                    cursor = _next_cursor(page.data)

            # Map Dropbox AccessLevel to PermissionType
            access_level_map = {
                'owner': PermissionType.OWNER,
                'editor': PermissionType.WRITE,
                'viewer': PermissionType.READ,
            }

            if hasattr(members_result.data, 'users') and members_result.data.users:
                for user_membership in members_result.data.users:
                    access_type_tag = user_membership.access_type._tag
                    permission_type = access_level_map.get(access_type_tag, PermissionType.READ)

                    user_info = user_membership.user

                    # Get email and check validity
                    email = user_info.email if hasattr(user_info, 'email') else None

                    # Skip users without email or with email ending in '#'
                    if not email or email.endswith('#'):
                        self.logger.debug(f"Skipping user {user_info.account_id} with invalid email: {email}")
                        continue

                    covered_emails.add(email)
                    if is_file and not member_is_direct(is_inherited=getattr(user_membership, "is_inherited", None)):
                        continue

                    permissions.append(Permission(
                        external_id=user_info.account_id,
                        email=email,
                        type=permission_type,
                        entity_type=EntityType.USER
                    ))

            # Process group permissions
            if hasattr(members_result.data, 'groups') and members_result.data.groups:
                for group_membership in members_result.data.groups:
                    if is_file and not member_is_direct(is_inherited=getattr(group_membership, "is_inherited", None)):
                        continue
                    access_type_tag = group_membership.access_type._tag
                    permission_type = access_level_map.get(access_type_tag, PermissionType.READ)

                    group_info = group_membership.group
                    permissions.append(Permission(
                        external_id=group_info.group_id,
                        email=None,  # Groups don't have emails
                        type=permission_type,
                        entity_type=EntityType.GROUP
                    ))

            if is_file and hasattr(members_result.data, "invitees"):
                for invitee_membership in members_result.data.invitees or []:
                    if not member_is_direct(is_inherited=getattr(invitee_membership, "is_inherited", None)):
                        continue
                    invitee = getattr(invitee_membership, "invitee", None)
                    email = getattr(invitee, "email", None)
                    if not email or str(email).endswith("#"):
                        continue
                    access_type = getattr(invitee_membership, "access_type", None)
                    access_type_tag = getattr(access_type, "_tag", "viewer")
                    permissions.append(Permission(
                        external_id=email,
                        email=email,
                        type=access_level_map.get(access_type_tag, PermissionType.READ),
                        entity_type=EntityType.USER,
                    ))

        except Exception as e:
            self.logger.warning(f"Error converting Dropbox permissions for {file_or_folder_id}: {e}")
            return None

        return DropboxGrants(permissions, covered_emails=covered_emails)


    # Update the _permissions_equal method (fix the comparison logic)

    def _permissions_equal(self, old_perms: List[Permission], new_perms: List[Permission]) -> bool:
        """
        Compare two lists of permissions to detect changes.
        """
        if not old_perms and not new_perms:
            return True
        if not old_perms or not new_perms:  # One is empty, the other is not
            return False
        if len(old_perms) != len(new_perms):
            return False

        # Create sets of permission tuples for comparison
        # Include all relevant fields that would indicate a permission change
        old_set = {(p.external_id, p.type.value, p.entity_type.value) for p in old_perms}
        new_set = {(p.external_id, p.type.value, p.entity_type.value) for p in new_perms}

        return old_set == new_set


    async def _process_users_in_batches(self, users: List[User]) -> None:
        """
        Process users in concurrent batches for improved performance.

        Args:
            users: List of users to process
        """
        try:
            # Get all active users
            all_active_users = await self.data_entities_processor.get_all_active_users()
            active_user_emails = {active_user.email.lower() for active_user in all_active_users}


            # Only an active member can open anything; a suspended member's calls are refused.
            users_to_sync = [
                user for user in users
                if user.email and user.email.lower() in active_user_emails
                and getattr(user, "is_active", True) is not False
            ]

            self.logger.info(f"Processing {len(users_to_sync)} active users out of {len(users)} total users")

            plan = await self._plan_shared_folders(users_to_sync)
            self._shared_folder_plan = plan
            await self._sync_shared_folder_groups(plan)
            nested = await self._relist_parents_of_new_nested_folders(plan)
            assigned: dict[str, list[str]] = {}
            for folder in plan.values():
                assigned.setdefault(folder.syncer, []).append(folder.folder_id)
            known_shared_folders = frozenset(plan)

            # Process users in concurrent batches
            for i in range(0, len(users_to_sync), self.max_concurrent_batches):
                batch = users_to_sync[i:i + self.max_concurrent_batches]

                # Run sync for batch of users concurrently
                sync_tasks = [
                    self._run_sync_with_yield(
                        user.source_user_id, user.email,
                        tuple(assigned.get(user.source_user_id, ())), known_shared_folders,
                    )
                    for user in batch
                ]

                for user, result in zip(batch, await asyncio.gather(*sync_tasks, return_exceptions=True)):
                    if isinstance(result, BaseException):
                        self.logger.error(f"Sync of {user.email}'s Dropbox failed: {result}", exc_info=result)
                        self._health.skipped(f"files of {user.email}", result)
                if self._health.auth_error is not None:
                    return

                # Small delay between batches to prevent overwhelming the API
                await asyncio.sleep(1)

            await self._drop_vanished_shared_folders(plan)
            if self._health.complete:
                await self.dropbox_cursor_sync_point.update_sync_point(
                    _NESTED_FOLDERS_KEY, sync_point_data={"ids": sorted(nested)}
                )
            self.logger.info("Completed processing all user batches")

        except Exception as e:
            self.logger.error(f"Error processing users in batches: {e}")
            raise



    async def _relist_parents_of_new_nested_folders(self, plan: dict[str, SharedFolder]) -> set[str]:
        """List from the start the parent of every nested folder no earlier run planned; return the nested ids.

        Until then the parent listed that folder's mount point and contents as its own (a folder that just
        stopped inheriting, or one an older version never planned). Its cursor reports no change for them, so
        only a listing from the start removes that copy. The nested folders already seen are remembered, so an
        empty one (which never has records) does not relist its parent on every run.
        """
        nested = {folder.folder_id for folder in plan.values() if folder.parent_id in plan}
        known = set((await self.dropbox_cursor_sync_point.read_sync_point(_NESTED_FOLDERS_KEY)).get("ids") or [])
        for folder_id in nested - known:
            self._fresh_folders.add(plan[folder_id].parent_id)
        return nested

    async def _plan_shared_folders(self, users: list[AppUser]) -> dict[str, SharedFolder]:
        """The team's shared folders, from every member's list, each with the member who lists it."""
        listings: dict[str, list] = {}
        for user in users:
            entries = await self._list_shared_folders(user.source_user_id)
            if entries is not None:
                listings[user.source_user_id] = entries
            else:
                self._health.skipped(f"shared folders of {user.email}", "could not be listed")
        plan = plan_shared_folders(listings)
        # A member whose list could not be read may be the one who has a folder in their Dropbox.
        self._plan_complete = len(listings) == len(users)
        self._folder_metadata: dict[str, DropboxResponse] = {}
        if self._plan_complete:
            await self._drop_unmounted_shared_folders(plan)
        await self._resolve_parents(plan)
        return plan

    async def _resolve_parents(self, plan: dict[str, SharedFolder]) -> None:
        """Read the parent of every folder listed inside a team folder without one.

        Until it is known, the folder's group is not rewritten: written as a top-level
        folder it would drop the members it inherits.
        """
        self._parent_unknown: set[str] = set()
        for folder in [folder for folder in plan.values() if folder.parent_unknown]:
            response = await self._shared_folder_metadata(folder)
            if not response.success:
                self.logger.warning(f"Could not read where shared folder '{folder.name}' ({folder.folder_id}) sits: "
                                    f"{response.error}")
                self._health.skipped(f"parent of shared folder {folder.name}", "could not be read; stored folder kept")
                self._parent_unknown.add(folder.folder_id)
                continue
            inheritance = getattr(getattr(response.data, "access_inheritance", None), "_tag", None) or folder.inheritance
            folder.set_parent(getattr(response.data, "parent_shared_folder_id", None), inheritance)

    async def _drop_vanished_shared_folders(self, plan: dict[str, SharedFolder]) -> None:
        """Delete the stored shared and team folders that no synced member can open any more.

        A folder that was unshared, archived (team folders), deleted, or left by every synced member is in no
        member's ``sharing/list_folders``. Only a complete answer counts: every synced member's list read, and
        the team's member list read whole; otherwise nothing is removed.
        """
        if not getattr(self, "_plan_complete", False) or not self._health.complete:
            return
        stored = await self.data_entities_processor.get_nodes_by_filters(
            CollectionNames.RECORD_GROUPS.value, {"connectorId": self.connector_id},
            ["externalGroupId", "description", "groupName"], raise_on_error=True,
        )
        for group in stored:
            external_id = group.get("externalGroupId")
            if group.get("description") not in ("Shared Folder", "Team Folder") or not external_id or external_id in plan:
                continue
            self.logger.info(f"Shared folder '{group.get('groupName')}' ({external_id}) is gone for every synced member; deleting it")
            await self.data_entities_processor.on_record_group_deleted(
                external_group_id=external_id, connector_id=self.connector_id, trash_live_records=True,
            )

    async def _drop_unmounted_shared_folders(self, plan: dict[str, SharedFolder]) -> None:
        """Leave out, and delete, the shared folders no member has in their Dropbox.

        A deleted shared folder stays in ``sharing/list_folders``, with no path for
        anyone, and its namespace still lists its files as live; Dropbox has no
        deleted flag for it. Team folders are synced from the team folder list.
        """
        gone = []
        for folder in plan.values():
            if folder.mounted or folder.is_team_folder:
                continue
            # A folder inside a team folder has no path on the team member root layout, members or not: it is
            # reached through the team folder, so having no path does not mean it is gone. Its own id does.
            if folder.inside_team_folder and not await self._deleted_inside_team_folder(folder):
                continue
            gone.append(folder)
        for folder in gone:
            del plan[folder.folder_id]
            try:
                stored = await self.data_entities_processor.get_record_group_by_external_id(
                    self.connector_id, folder.folder_id
                )
                if stored is not None:
                    self.logger.info(f"Shared folder '{folder.name}' ({folder.folder_id}) is in no member's Dropbox; deleting it")
                    await self.data_entities_processor.on_record_group_deleted(
                        external_group_id=folder.folder_id, connector_id=self.connector_id, trash_live_records=True,
                    )
            except Exception as e:
                self.logger.error(f"Could not delete shared folder '{folder.name}' ({folder.folder_id}): {e}", exc_info=True)

    async def _deleted_inside_team_folder(self, folder: SharedFolder) -> bool:
        """Whether Dropbox says a shared folder inside a team folder was deleted: its folder id is not_found.

        Its namespace still lists the files after the delete, and ``sharing/get_folder_metadata`` still answers,
        so the folder's own id is the only sign. ``sharing/list_folders`` leaves that id out; the folder's metadata
        has it. Without an answer the folder is kept.
        """
        metadata = await self._shared_folder_metadata(folder)
        item_id = getattr(metadata.data, "folder_id", None) if metadata.success else None
        answer = item_still_exists(await self.data_source.files_get_metadata(
            path=item_id, team_member_id=folder.syncer, team_folder_id=folder.folder_id,
        )) if item_id else None
        if answer is None:
            self.logger.warning(f"Could not check whether shared folder '{folder.name}' ({folder.folder_id}) "
                                "still exists; keeping it")
        return answer is False

    async def _shared_folder_metadata(self, folder: SharedFolder) -> DropboxResponse:
        """``sharing/get_folder_metadata`` as the folder's syncer, read once per run."""
        cache = getattr(self, "_folder_metadata", {})
        if folder.folder_id not in cache:
            cache[folder.folder_id] = await self.data_source.sharing_get_folder_metadata(
                shared_folder_id=folder.folder_id, team_member_id=folder.syncer
            )
        return cache[folder.folder_id]

    async def _list_shared_folders(self, member_id: str) -> list | None:
        """A member's shared folders, every page; None when Dropbox could not be read."""
        response = await self.data_source.sharing_list_folders(team_member_id=member_id)
        if not response.success:
            self.logger.warning(f"Could not list the shared folders of {member_id}: {response.error}")
            if is_auth_error(response.error):
                self._health.auth_failed(response.error)
            return None
        entries = list(response.data.entries)
        cursor = _next_cursor(response.data)
        while cursor:
            response = await self.data_source.sharing_list_folders_continue(cursor, team_member_id=member_id)
            if not response.success:
                self.logger.warning(f"Could not list every shared folder of {member_id}: {response.error}")
                return None
            entries.extend(response.data.entries)
            cursor = _next_cursor(response.data)
        return entries

    async def _sync_shared_folder_groups(self, plan: dict[str, SharedFolder]) -> None:
        """Write each shared folder as a record group under its own name, granted to its members.

        Members are read every time, in place of the stored grants: a member who left
        can no longer read the folder, so the event that says so can't be relied on.
        Team folders are written the same way: their members come from the same call,
        which needs no team-content permission (team/team_folder/list needs
        team_data.content.read, which the connector does not ask for).
        """
        for folder in parents_first(plan):
            if folder.folder_id in getattr(self, "_parent_unknown", set()):
                continue
            try:
                if not await self.data_entities_processor.get_records_in_record_group(
                    self.connector_id, folder.folder_id, 1
                ):
                    self._fresh_folders.add(folder.folder_id)
                grants = await self._shared_folder_members(folder.folder_id, folder.syncer)
                if grants is None:
                    self._health.skipped(f"members of shared folder {folder.name}", "could not be read; stored access kept")
                else:
                    self._note_grant_emails(grants)
                record_group = RecordGroup(
                    name=folder.name,
                    org_id=self.data_entities_processor.org_id,
                    external_group_id=folder.folder_id,
                    parent_external_group_id=folder.parent_id,
                    description="Team Folder" if folder.is_team_folder else "Shared Folder",
                    connector_name=self.connector_name,
                    connector_id=self.connector_id,
                    group_type=RecordGroupType.DRIVE,
                    inherit_permissions=folder.inherits,
                )
                await self.data_entities_processor.on_new_record_groups([(record_group, grants)])
            except Exception as e:
                self.logger.error(f"Could not write shared folder '{folder.name}' ({folder.folder_id}): {e}", exc_info=True)
                self._health.skipped(f"shared folder {folder.name}", e)

    async def _shared_folder_members(self, folder_id: str, member_id: str) -> list[Permission] | None:
        """A shared folder's own members as grants, every page; None when they could not all be read."""
        response = await self.data_source.sharing_list_folder_members(shared_folder_id=folder_id, team_member_id=member_id)
        if not response.success:
            self.logger.error(
                f"Could not read the members of shared folder {folder_id}, so its stored grants stay: {response.error}"
            )
            return None
        users, groups, invitees = list(response.data.users), list(response.data.groups), list(response.data.invitees)
        cursor = _next_cursor(response.data)
        while cursor:
            response = await self.data_source.sharing_list_folder_members_continue(cursor=cursor, team_member_id=member_id)
            if not response.success:
                self.logger.error(
                    f"Could not read every member of shared folder {folder_id}, so its stored grants stay: {response.error}"
                )
                return None
            users.extend(response.data.users)
            groups.extend(response.data.groups)
            invitees.extend(response.data.invitees)
            cursor = _next_cursor(response.data)
        return folder_grants(users, groups, invitees)

    async def _refresh_shared_folder(self, folder_id: str, event_member_id: str) -> None:
        """Re-read one shared folder's name, place and members after a sharing event on it.

        It is read as the member who lists it this run: the event's member may be the
        one who just left. A folder this run did not list (shared since) has only them.
        """
        planned = self._shared_folder_plan.get(folder_id)
        member_id = planned.syncer if planned else event_member_id
        response = await self.data_source.sharing_get_folder_metadata(shared_folder_id=folder_id, team_member_id=member_id)
        if not response.success:
            self.logger.error(
                f"Could not read shared folder {folder_id} as {member_id} after a sharing event, so its stored "
                f"grants stay until a full sync re-reads its members: {response.error}"
            )
            return
        await self._sync_shared_folder_groups(plan_shared_folders({member_id: [response.data]}))

    async def _drop_mount_record(self, entry: object, record_group_id: str) -> None:
        """Remove a record an earlier sync wrote for a shared folder's mount point; the folder is a record group now.

        A folder that becomes a shared folder (shared, or its inherited access removed) is listed in its parent
        under a new id at the same path, while its old id becomes the shared folder's own and still resolves
        (dbx-audit/live, 2026-10-29). So the folder record stored at that path in this listing's group goes too:
        left behind, it stays in the parent, open to the parent's members.
        """
        info = getattr(entry, "sharing_info", None)
        if not isinstance(entry, FolderMetadata) or not getattr(info, "shared_folder_id", None):
            return
        stale = {}
        stored = await self.data_entities_processor.get_record_by_external_id(self.connector_id, entry.id)
        if stored is not None:
            stale[stored.id] = stored
        for record in await self.data_entities_processor.get_records_by_file_path(self.connector_id, entry.path_lower):
            if record.external_record_group_id == record_group_id:
                stale[record.id] = record
        for record_id in stale:
            await self.data_entities_processor.on_record_deleted(record_id=record_id)

    async def _run_sync_with_yield(
        self, user_id: str, user_email: str,
        shared_folder_ids: tuple[str, ...] = (), known_shared_folders: frozenset[str] = frozenset(),
    ) -> None:
        """
        Synchronizes Dropbox files for a given user using the cursor-based approach.

        The member's personal folder (root) and each shared folder this member lists for
        the team are listed separately, each with its own cursor.

        Args:
            user_id: The Dropbox team member ID of the user to sync.
            user_email: The email of the user to sync.
        """
        self.logger.info(f"Starting Dropbox sync with yield for user {user_email}")
        self.logger.info(f"Will sync {len(shared_folder_ids)} shared folder(s) as {user_email}")
        for folder_id in [None, *shared_folder_ids]:
            await self._sync_namespace(user_id, user_email, folder_id, known_shared_folders)
            if self._health.auth_error is not None:
                return
        self.logger.info(f"Completed all Dropbox sync loops for user {user_id}")

    async def _sync_namespace(
        self, user_id: str, user_email: str, folder_id: str | None, known_shared_folders: frozenset[str],
    ) -> None:
        """List one namespace (a member's home, or a shared folder) from its cursor and write what changed.

        A page is written before its cursor is saved, so a run cut off part way resumes there. A cursor Dropbox
        reset is dropped and the namespace listed from the start; once that listing has finished, what it no longer
        shows is checked and removed. A page that cannot be read ends this namespace for this run and is recorded,
        so the run is not reported as a success and a full sync removes nothing.
        """
        # _v3: a listing written by an earlier version is read once more from the start, and what it no longer
        # shows is removed, to repair what that version stored (parents lost to a child listed before its
        # folder, paths left by moves, records of replaced files, team folders without their members).
        context = user_id if folder_id is None else f"{user_id}_{folder_id}"
        group = "users" if folder_id is None else "shared_folders"
        sync_point_key = generate_record_sync_point_key(RecordType.DRIVE.value, f"{group}_v3", context)
        if folder_id is None:
            sync_log_name = f"personal folder for user {user_email}"
            record_group_id = user_id
        else:
            sync_log_name = f"shared folder {folder_id} for user {user_email}"
            record_group_id = folder_id
        sync_point = await self.dropbox_cursor_sync_point.read_sync_point(sync_point_key)
        older = sync_point.get('cursor') is None and bool((await self.dropbox_cursor_sync_point.read_sync_point(
            generate_record_sync_point_key(RecordType.DRIVE.value, f"{group}_v2", context))).get('cursor'))
        # A folder with no live record (new, shared again, restored from the archive) is listed in full: an old
        # cursor would list only what changed since, and its records went with the group when it was deleted.
        cursor = None if folder_id in self._fresh_folders else sync_point.get('cursor')
        dates = self._get_date_filters()
        parents = ParentResolver(
            stored_id_at=lambda path: self._stored_id_at(path, record_group_id),
            ask_dropbox=lambda path: self._ask_parent_id(path, user_id, folder_id),
        )
        problems_before = len(self._health.problems)
        # A listing from the start (a full sync, a new or restored folder) shows what exists, not what was deleted
        # since the last cursor, which Dropbox reports only once: what it does not show is checked once it ends.
        relisting = older or cursor is None
        listed: set[str] = set()
        held = restart = False
        self.logger.info(f"Starting sync loop for: {sync_log_name} (cursor: {bool(cursor)})")
        while True:
            try:
                async with self.rate_limiter:
                    if cursor:
                        result = await self.data_source.files_list_folder_continue(
                            cursor, team_member_id=user_id, team_folder_id=folder_id,
                        )
                    else:
                        result = await self.data_source.files_list_folder(
                            path="", team_member_id=user_id, team_folder_id=folder_id, recursive=True,
                            include_has_explicit_shared_members=True,
                        )
            except Exception as e:  # the data source normally answers failures, it does not raise
                result = DropboxResponse(success=False, error=str(e))
            if not result.success:
                if cursor and is_cursor_reset(result.error):
                    self.logger.warning(f"[{sync_log_name}] Dropbox reset the cursor; listing it again from the start")
                    self._health.relisted.append(sync_log_name)
                    relisting, cursor = True, None
                    listed.clear()
                    continue
                if is_auth_error(result.error):
                    self._health.auth_failed(result.error)
                    return
                self.logger.error(f"[{sync_log_name}] Dropbox API call failed: {result.error}")
                self._health.skipped(f"listing of {sync_log_name}", result.error)
                return

            entries = result.data.entries
            self.logger.info(f"[{sync_log_name}] Got {len(entries)} entries. Has_more: {result.data.has_more}")
            page_problems = len(self._health.problems)
            updates = []
            async for _record, _permissions, record_update in self._process_dropbox_items_generator(
                entries, user_id, user_email, record_group_id, folder_id is None,
                modified_after=dates[0], modified_before=dates[1], created_after=dates[2], created_before=dates[3],
                shared_folder_ids=known_shared_folders, parents=parents,
            ):
                updates.append(record_update)
                listed.add(record_update.external_record_id)
            if relisting:
                listed.update(getattr(e, "id", None) for e in entries if not isinstance(e, DeletedMetadata))
            await self._write_updates(updates, sync_log_name)
            # A moved folder whose paths could not all be rewritten: listed again from the start, which writes
            # every path below it. Saved now: replayed from an older cursor, the move would no longer show.
            if not await self._repath_moved_folders():
                restart = True
                await self.dropbox_cursor_sync_point.update_sync_point(sync_point_key, sync_point_data={"cursor": None})

            cursor = result.data.cursor
            # Dropbox lists a change once: after a page with a failed entry the saved cursor stays before it, and
            # the next run reads it again (its writes are idempotent). The pages after it are still written.
            held = held or len(self._health.problems) > page_problems
            if not (held or restart):
                await self.dropbox_cursor_sync_point.update_sync_point(sync_point_key, sync_point_data={"cursor": cursor})
            if result.data.has_more is not True:
                break

        if relisting and len(self._health.problems) == problems_before:
            outcome = await remove_unlisted(
                self.data_entities_processor, self.connector_id, record_group_id, listed,
                lambda record: self._team_item_still_exists(record, user_id), self.logger,
            )
            self._note_delete_outcome(outcome)
        self.logger.info(f"Completed sync loop for: {sync_log_name}")

    async def _write_updates(self, updates: List[RecordUpdate], sync_log_name: str) -> None:
        """Write a page's records in the order Dropbox listed them.

        Records whose own members were read (or are known to be none) are written with them, in one batch: a
        moved, renamed or edited record and a folder made in the same change land together, so a child never
        waits for a parent that is not stored yet. A record whose members could not be read keeps its stored
        grants: it is written as a metadata or content update, which leaves them alone.
        """
        batch: list[tuple[FileRecord, list[Permission]]] = []

        async def flush() -> None:
            if batch:
                self.logger.info(f"[{sync_log_name}] Processing batch of {len(batch)} records.")
                await self.data_entities_processor.on_new_records(list(batch))
                batch.clear()

        for update in updates:
            if update.new_permissions is not None or update.is_new:
                batch.append((update.record, list(update.new_permissions or [])))
                if len(batch) >= self.batch_size:
                    await flush()
                continue
            await flush()
            if update.content_changed:
                await self.data_entities_processor.on_record_content_update(update.record)
            else:
                await self.data_entities_processor.on_record_metadata_update(update.record)
        await flush()

    async def _handle_record_updates(self, record_update: RecordUpdate) -> None:
        """Write one re-read record (after a sharing event, or a reindex that found it changed), or delete one."""
        if record_update.is_deleted:
            # The update carries Dropbox's id; records are deleted by their key.
            existing = await self.data_entities_processor.get_record_by_external_id(
                self.connector_id, record_update.external_record_id
            )
            if existing:
                await self.data_entities_processor.on_record_deleted(record_id=existing.id)
            return
        await self._write_updates([record_update], "re-read")
        await self._repath_moved_folders()

    async def _repath_moved_folders(self) -> bool:
        """Rewrite the stored paths below each folder that moved; False when a walk stopped at its cap."""
        complete = True
        moved, self._moved_folders = self._moved_folders, []
        for external_id, old_path, new_path in moved:
            _, done = await repath_descendants(self.data_entities_processor, self.connector_id, external_id,
                                               old_path, new_path, self.logger)
            if not done:
                self._health.skipped(f"paths below {new_path}", "could not all be rewritten; listed again next run")
            complete = complete and done
        return complete

    def get_app_users(self, users: DropboxResponse | List) -> List[AppUser]:
        members = users if isinstance(users, list) else users.data.members
        app_users: List[AppUser] = []
        for member in members:
            profile = member.profile
            app_users.append(
                AppUser(
                    app_name=self.connector_name,
                    connector_id=self.connector_id,
                    source_user_id=profile.team_member_id,
                    full_name=profile.name.display_name,
                    email=profile.email,
                    is_active=(profile.status._tag == "active"),
                    source_role=member.role._tag,
                )
            )
        return app_users

    async def _register_authenticated_identity(self) -> None:
        """Record which source account this connector is authenticated as, so a creator whose
        PipesHub email differs still resolves that account's permissions for this connector."""
        if not self.data_source:
            return
        profile = None
        try:
            response = await self.data_source.team_token_get_authenticated_admin()
            if response.success:
                profile = getattr(response.data, "admin_profile", None)
            else:
                self.logger.debug("Dropbox did not return the authenticated admin: %s", response.error)
                if is_auth_error(response.error):
                    self._health.auth_failed(response.error)
        except Exception as e:
            self.logger.debug("Could not read the authenticated Dropbox admin: %s", e)
            return
        await self.register_authenticated_source_user(
            getattr(profile, "email", None), getattr(profile, "team_member_id", None)
        )

    async def _sync_members(self) -> List[AppUser]:
        """Every member of the team, saved as the connector's users, and the access of those who can no longer
        open anything (suspended, invited, removed) withdrawn. Returns the members."""
        members = await read_team_members(self.data_source)
        if members.auth_error:
            self._health.auth_failed(members.error)
            return []
        if not members.raw:
            raise RuntimeError(f"Could not list the Dropbox team's members: {members.error}")
        if not members.complete:
            self._health.skipped("team member list", members.error)
        app_users = self.get_app_users(members.raw)
        self._team_emails = {u.email.lower() for u in app_users if u.email}
        await self.data_entities_processor.on_new_app_users(app_users)
        if members.complete:
            stored = await self.data_entities_processor.get_all_app_users(self.connector_id, raise_on_error=True)
            gone = withdrawn(stored, members)
            if gone:
                self.logger.info(f"Withdrawing Dropbox access from {len(gone)} member(s) who are not active")
                await self.data_entities_processor.withdraw_app_user_access(self.connector_id, gone)
            # A member no longer on the team at all has no Dropbox home to read; a suspended one keeps theirs,
            # since what they shared stays shared (Dropbox Help: "sharing relationships remain intact").
            listed = {m.profile.team_member_id for m in members.raw}
            for user in gone:
                if user.source_user_id not in listed:
                    self.logger.info(f"{user.email} left the team; removing their Dropbox home")
                    await self.data_entities_processor.on_record_group_deleted(
                        external_group_id=user.source_user_id, connector_id=self.connector_id,
                        trash_live_records=True,
                    )
        return app_users

    def full_sync_coverage(self) -> FullSyncCoverage:
        """A run that skipped part of Dropbox did not see everything, so a full sync must remove nothing."""
        return FullSyncCoverage(complete=self._health.complete)

    async def run_sync(self) -> None:
        """Runs a sync from the Dropbox account root: each folder from its cursor, or in full after a full-sync request.

        A run that could not read part of Dropbox, or whose credentials Dropbox refused, raises at the end: it is
        recorded as failed, a full sync removes nothing, and the people who own the connector are told once.
        """
        self._health = SyncHealth()
        self._external_emails = set()
        self._activity_log_missing: str | None = None
        # Found again each run from what is stored: a folder is listed from the start only in the run that finds
        # it fresh, and a move a stopped run did not finish is listed again, its cursor not having passed it.
        self._fresh_folders = set()
        self._moved_folders = []
        try:
            await self._sync_steps()
        except Exception as ex:
            self.logger.error(f"❌ Error in DropBox connector run: {ex}")
            if is_auth_error(ex):
                self._health.auth_failed(ex)
            else:
                self._health.skipped("the sync", ex)
            raise
        finally:
            self.last_sync_summary = {"skipped": len(self._health.problems), "relisted": len(self._health.relisted)}
            await SyncNotices(self).report(self._health, activity_log_missing=self._activity_log_missing)
        self._health.raise_if_incomplete()

    async def _sync_steps(self) -> None:
        activity_log_missing: str | None = None
        self.logger.info("Starting Dropbox full sync.")

        await self._register_authenticated_identity()
        if self._health.auth_error is not None:
            return

        self.sync_filters, self.indexing_filters = await load_connector_filters(
            self.config_service, "dropbox", self.connector_id, self.logger
        )

        # Step 1: every member, every run: who is on the team and who may still read is read again each time.
        self.logger.info("Syncing users...")
        app_users = await self._sync_members()
        if self._health.auth_error is not None:
            return

        # Step 2: fetch and sync all user groups
        group_sync_key = generate_record_sync_point_key("user_group_events", "team_events", "global")
        group_sync_point = await self.dropbox_cursor_sync_point.read_sync_point(group_sync_key)

        if not group_sync_point.get('cursor'):
            self.logger.info("Running a FULL sync for user groups...")
            # The cursor is taken before the groups are read, so a change made while they are read is
            # listed by the next run.
            activity_log_missing = await self._initialize_event_cursor(group_sync_key, EventCategory.groups)
            await self._sync_user_groups()
        else:
            self.logger.info("Running an INCREMENTAL sync for user groups...")
            await self._sync_group_changes_with_cursor()

        # Step 3: team folder events (renames, archives, deletes).
        record_group_sync_key = generate_record_sync_point_key("record_group_events", "team_events", "global")
        record_group_sync_point = await self.dropbox_cursor_sync_point.read_sync_point(record_group_sync_key)
        if not record_group_sync_point.get('cursor'):
            await self._initialize_event_cursor(record_group_sync_key, EventCategory.team_folders)
        else:
            await self._sync_record_group_changes_with_cursor(app_users)

        await self.sync_personal_record_groups(app_users)

        # Deletes earlier runs could not confirm.
        owed = await self._owed.load()
        try:
            if owed:
                self._owed.settle(await recheck_owed_deletes(
                    self.data_entities_processor, self.connector_id, owed,
                    lambda record: self._team_item_still_exists(record, None), self.logger,
                ))

            # Step 4: fetch and sync all user drives (shared and team folders are record groups, written first).
            self.logger.info("Syncing User Drives")
            await self._process_users_in_batches(app_users)
            if self._health.auth_error is not None:
                return

            # Step 4.5 sync for permissions changes
            sharing_sync_key = generate_record_sync_point_key("sharing_events", "team_events", "global")
            sharing_sync_point = await self.dropbox_cursor_sync_point.read_sync_point(sharing_sync_key)

            if not sharing_sync_point.get('cursor'):
                self.logger.info("Initializing cursor for sharing events...")
                activity_log_missing = await self._initialize_event_cursor(sharing_sync_key, EventCategory.sharing) \
                    or activity_log_missing
            else:
                self.logger.info("Running an INCREMENTAL sync for sharing events...")
                await self._sync_sharing_changes_with_cursor(sharing_sync_key, sharing_sync_point.get('cursor'))

            # People outside the team that something is shared with reach the app only through this membership.
            if self._external_emails:
                await self.data_entities_processor.on_external_app_users(sorted(self._external_emails), self.connector_id)
            if self._health.complete:
                await self.data_entities_processor.reap_external_app_users(self.connector_id)
        finally:
            # Deletes owed so far are kept even when this run stops part way.
            await self._owed.save()
        self._activity_log_missing = activity_log_missing

        self.logger.info("Dropbox full sync completed.")

    async def _initialize_event_cursor(self, sync_key: str, category: EventCategory,
                                       start_time: datetime | None = None,
                                       pending: dict[str, list[str]] | None = None) -> str | None:
        """
        Take a cursor of the team's activity log from now (or from ``start_time``) on.

        A write replaces the stored document, so the work still owed beside the cursor (its ``pending*`` lists)
        is written with the new cursor: ``pending`` when given, else what is stored.

        Returns Dropbox's error when the log cannot be read (a plan without it, or no events.read), else None.
        """
        try:
            from dropbox.team_common import TimeRange

            current_time = start_time or datetime.now(timezone.utc)
            # TimeRange with only start_time means "from then onwards"
            response = await self.data_source.team_log_get_events(
                category=category,
                time=TimeRange(start_time=current_time),
                limit=1
            )

            if response.success and hasattr(response.data, 'cursor') and response.data.cursor:
                if pending is None:
                    stored = await self.dropbox_cursor_sync_point.read_sync_point(sync_key) or {}
                    pending = {key: value for key, value in stored.items() if key.startswith("pending")}
                await self.dropbox_cursor_sync_point.update_sync_point(
                    sync_key, {"cursor": response.data.cursor, **pending}
                )
                self.logger.info(f"✓ Initialized cursor for {category} from {current_time}")
                return None
            error = response.error if not response.success else 'No cursor returned'
            self.logger.warning(f"Could not initialize cursor for {category}: {error}")
            if is_auth_error(error) and not missing_scope(error):
                self._health.auth_failed(error)
            return str(error)
        except Exception as e:
            self.logger.error(f"Could not initialize event cursor for {category}: {e}", exc_info=True)
            return str(e)

    async def _sync_user_groups(self) -> None:
        """
        Syncs all Dropbox groups and their members, collecting them into a
        single batch before sending to the processor.
        """
        try:
            self.logger.info("Starting Dropbox user group synchronization")

            # --- 1. Get all groups, with pagination ---
            # A list that could not be read whole writes nothing: the stored groups stay, and the run is not a
            # success, so a full sync does not remove the groups it did not see.
            all_groups_list = []
            groups_response = await self.data_source.team_groups_list()
            while True:
                if not groups_response.success:
                    self.logger.error(f"❌ Failed to fetch the group list: {groups_response.error}")
                    if is_auth_error(groups_response.error):
                        self._health.auth_failed(groups_response.error)
                    else:
                        self._health.skipped("group list", groups_response.error)
                    return
                all_groups_list.extend(groups_response.data.groups)
                if not groups_response.data.has_more:
                    break
                self.logger.info("Fetching more groups...")
                groups_response = await self.data_source.team_groups_list_continue(groups_response.data.cursor)

            self.logger.info(f"Found {len(all_groups_list)} total groups. Now processing members.")

            # --- 2. Define permission mapping (similar to record_groups) ---
            # Dropbox group members are either 'owner' or 'member'

            # This will hold our final list of tuples: List[Tuple[AppUserGroup, List[Permission]]]
            user_groups_batch = []

            # --- 3. Loop through all groups to build the batch (NO processor calls inside loop) ---
            for group in all_groups_list:
                try:
                    # Partial members would replace the stored ones and take access from everyone not read.
                    all_members = await self._fetch_group_members(group.group_id, group.group_name,
                                                                  raise_on_partial=True)

                    processor_group, member_permissions = self._create_user_group_with_permissions(
                        group.group_id, group.group_name, all_members
                    )
                    user_groups_batch.append((processor_group, member_permissions))

                except Exception as e:
                    self.logger.error(f"❌ Failed to process group {group.group_name}: {e}", exc_info=True)
                    self._health.skipped(f"members of group {group.group_name}", e)
                    continue  # Skip this group and move to the next

            # --- 4. Send the ENTIRE batch to the processor ONCE (outside the loop) ---
            if user_groups_batch:
                self.logger.info(f"Submitting {len(user_groups_batch)} user groups to the processor...")
                await self.data_entities_processor.on_new_user_groups(user_groups_batch)
                self.logger.info("Successfully submitted batch to on_new_user_groups.")
            else:
                self.logger.info("No user groups found or processed.")

            self.logger.info("Completed Dropbox user group synchronization.")

        except Exception as e:
            self.logger.error(f"❌ Fatal error in _sync_dropbox_groups: {e}", exc_info=True)
            raise

    async def _sync_group_changes_with_cursor(self) -> None:
        """
        Syncs user group changes incrementally using the team event log cursor.
        """
        try:
            self.logger.info("Starting incremental sync for user groups...")

            # 1. Define a single, global key for the team-wide group event cursor
            sync_point_key = generate_record_sync_point_key(
                "user_group_events", "team_events", "global"
            )

            # 2. Get the last saved cursor from your database
            sync_point = await self.dropbox_cursor_sync_point.read_sync_point(sync_point_key)
            cursor = sync_point.get('cursor')

            if not cursor:
                self.logger.warning("No cursor found for incremental group sync. Running full sync instead.")
                await self._sync_user_groups()
                return

            has_more = True
            latest_cursor_to_save = cursor
            events_processed = 0
            # Before the new events, so that they are applied on top of the corrected groups.
            owed_member_reads, owed_deletes = await self._retry_owed_group_removals(sync_point)

            while has_more:
                try:
                    # 3. Fetch the latest events from the Dropbox audit log
                    async with self.rate_limiter:
                        response = await self.data_source.team_log_get_events_continue(cursor)

                    if not response.success:
                        self.logger.error(f"⚠️ Error fetching team event log: {response.error}")
                        if is_cursor_reset(response.error):
                            # Events since the cursor are lost: read every group again, and take a new cursor.
                            # Removals still owed are kept with it: Dropbox does not list their events again.
                            pending = {"pendingGroupMemberReads": owed_member_reads,
                                       "pendingGroupDeletes": owed_deletes}
                            if await self._initialize_event_cursor(sync_point_key, EventCategory.groups,
                                                                   pending=pending) is not None:
                                # No new cursor: the old one, which resets again next run, keeps them.
                                await self.dropbox_cursor_sync_point.update_sync_point(
                                    sync_point_key, sync_point_data={"cursor": cursor, **pending})
                            await self._sync_user_groups()
                            return
                        self._health.skipped("group changes", response.error)
                        break

                    events = response.data.events
                    self.logger.info(f"Processing {len(events)} new group-related events.")

                    # 4. Process each event individually
                    for event in events:
                        try:
                            await self._process_group_event(event)
                            events_processed += 1
                        except GroupAccessRemovalError as e:
                            # The cursor moves on and Dropbox won't list the event again, so
                            # the group is queued with the cursor: every later run tries again
                            # until what the event should have removed is gone.
                            owed = owed_deletes if e.group_deleted else owed_member_reads
                            if e.group_id not in owed:
                                owed.append(e.group_id)
                            self.logger.error(f"❌ {e}; it will be tried again on the next run", exc_info=True)
                            continue
                        except Exception as e:
                            self.logger.error(f"Error processing group event: {e}", exc_info=True)
                            continue

                    # 5. Update state for the next loop iteration and for final saving
                    latest_cursor_to_save = response.data.cursor
                    has_more = response.data.has_more
                    cursor = latest_cursor_to_save

                except Exception as e:
                    self.logger.error(f"⚠️ Error in group sync loop: {e}", exc_info=True)
                    has_more = False

            # 6. Save the final, most recent cursor back to the database
            if latest_cursor_to_save:
                self.logger.info(f"Storing latest group sync cursor for key {sync_point_key}")
                await self.dropbox_cursor_sync_point.update_sync_point(
                    sync_point_key,
                    sync_point_data={
                        "cursor": latest_cursor_to_save,
                        "pendingGroupMemberReads": owed_member_reads,
                        "pendingGroupDeletes": owed_deletes,
                    }
                )

            self.logger.info(f"Incremental group sync completed. Processed {events_processed} events.")

        except Exception as e:
            self.logger.error(f"⚠️ Fatal error in incremental group sync: {e}", exc_info=True)
            raise

    async def _retry_owed_group_removals(self, sync_point: dict) -> tuple[list[str], list[str]]:
        """
        Try again the group deletions and member removals that earlier runs could not save.

        Returns the ids of the groups whose members still have to be read again, and
        of the groups that still have to be deleted.
        """
        still_to_delete: list[str] = []
        for group_id in sync_point.get('pendingGroupDeletes') or []:
            try:
                await self.data_entities_processor.on_user_group_deleted(
                    external_group_id=group_id,
                    connector_id=self.connector_id
                )
                self.logger.info(f"Deleted group {group_id}, which an earlier run could not delete")
            except Exception as e:
                self.logger.error(
                    f"❌ Deleted group {group_id} still could not be removed, so its members keep "
                    f"its access; will try again next run: {e}",
                    exc_info=True,
                )
                still_to_delete.append(group_id)

        still_to_read: list[str] = []
        for group_id in sync_point.get('pendingGroupMemberReads') or []:
            try:
                await self._replace_stored_group_members(group_id)
            except Exception as e:
                self.logger.error(
                    f"❌ The members of group {group_id} still could not be read and saved, so a member "
                    f"removed from it keeps its access; will try again next run: {e}",
                    exc_info=True,
                )
                still_to_read.append(group_id)

        return still_to_read, still_to_delete

    async def _replace_stored_group_members(self, group_id: str) -> None:
        """
        Read a stored group's current members from Dropbox and save them in place of the
        stored ones, which removes anyone a failed removal left behind. Raises if it can't.
        """
        stored_group = await self.data_entities_processor.get_user_group_by_external_id(
            connector_id=self.connector_id,
            external_id=group_id,
            raise_on_error=True,
        )
        if stored_group is None:
            # Deleted since, so there is no membership left to correct.
            return

        members = await self._fetch_group_members(group_id, stored_group.name, raise_on_partial=True)
        await self.data_entities_processor.on_new_user_groups(
            [self._create_user_group_with_permissions(group_id, stored_group.name, members)]
        )
        self.logger.info(f"Saved the current members of group {group_id}, which an earlier run could not correct")

    async def _process_group_event(self, event) -> None:
        """
        Process a single group-related event from the Dropbox audit log.
        Based on the actual API response structure.
        """
        try:
            # Log the full event for debugging
            self.logger.debug(f"Processing event: {event}")

            event_type = event.event_type._tag

            if event_type == "group_create":
                await self._handle_group_created_event(event)
            elif event_type == "group_delete":
                await self._handle_group_deleted_event(event)
            elif event_type in ["group_add_member", "group_remove_member"]:
                await self._handle_group_membership_event(event, event_type)
            elif event_type in ["group_rename"]:
                await self._handle_group_renamed_event(event)
            elif event_type in ["group_change_member_role"]:
                await self._handle_group_change_member_role_event(event)
            else:
                self.logger.debug(f"Ignoring event type: {event_type}")

        except GroupAccessRemovalError:
            raise
        except Exception as e:
            self.logger.error(f"Error processing group event of type {getattr(event, 'event_type', 'unknown')}: {e}", exc_info=True)

    async def _handle_group_membership_event(self, event, event_type: str) -> None:
        group_id, group_name = None, None
        member_email, member_name = None, None

        # 1. Extract common information (group and user details)
        try:
            for participant in event.participants:
                if participant.is_group():
                    group_info = participant.get_group()
                    group_id = group_info.group_id
                    group_name = group_info.display_name
                elif participant.is_user():
                    user_info = participant.get_user()
                    member_email = user_info.email
                    member_name = user_info.display_name
        except Exception as e:
            self.logger.error(f"Failed to parse participants for event {event_type}: {e}", exc_info=True)
            return

        # 2. Validate that we have the necessary IDs
        if not group_id or not member_email:
            self.logger.warning(f"Could not extract required group_id or member_email from {event_type} event. Skipping.")
            return

        # 3. Perform the appropriate action based on event_type
        if event_type == "group_add_member":
            self.logger.info(f"Adding member '{member_name}' ({member_email}) to group '{group_name}' ({group_id})")

            # Determine permission type (specific to 'add' events)
            permission_type = PermissionType.WRITE  # Default permission
            if hasattr(event.details, 'is_group_owner') and event.details.is_group_owner:
                permission_type = PermissionType.OWNER

            await self.data_entities_processor.on_user_group_member_added(
                external_group_id=group_id,
                user_email=member_email,
                permission_type=permission_type,
                connector_id=self.connector_id
            )

        elif event_type == "group_remove_member":
            self.logger.info(f"Removing member '{member_name}' ({member_email}) from group '{group_name}' ({group_id})")

            try:
                await self.data_entities_processor.on_user_group_member_removed(
                    external_group_id=group_id,
                    user_email=member_email,
                    connector_id=self.connector_id
                )
            except Exception as e:
                raise GroupAccessRemovalError(
                    f"Could not remove {member_email} from group '{group_name}' ({group_id}): {e}",
                    group_id, group_deleted=False,
                ) from e

    async def _handle_group_deleted_event(self, event) -> None:
        """Handle group_delete events from Dropbox audit log."""
        # Extract group_id from participants
        group_id = None
        group_name = None

        for participant in event.participants:
            if participant.is_group():
                group_info = participant.get_group()
                group_id = group_info.group_id
                group_name = group_info.display_name
                self.logger.info(f"Extracted deleted group: {group_name} ({group_id})")
                break

        # Validate we have required information
        if not group_id:
            self.logger.warning("Could not extract group_id from group_delete event")
            return

        self.logger.info(f"Deleting group {group_name} ({group_id})")

        try:
            await self.data_entities_processor.on_user_group_deleted(
                external_group_id=group_id,
                connector_id=self.connector_id
            )
        except Exception as e:
            raise GroupAccessRemovalError(
                f"Could not delete group '{group_name}' ({group_id}): {e}", group_id, group_deleted=True
            ) from e

    async def _handle_group_created_event(self, event) -> None:
        """Handle group_create events from Dropbox audit log."""
        # Extract group info from event participants
        group_id = None
        group_name = None

        for participant in event.participants:
            if participant.is_group():
                group_info = participant.get_group()
                group_id = group_info.group_id
                group_name = group_info.display_name
                break

        if not group_id:
            self.logger.warning("Could not extract group_id from group_create event")
            return

        self.logger.info(f"Creating group {group_name} ({group_id})")

        try:
            # Process the single newly created group
            await self._process_single_group(group_id, group_name)

        except Exception as e:
            self.logger.error(f"Error processing group_create event for group {group_id}: {e}", exc_info=True)

    async def _process_single_group(self, group_id: str, group_name: str) -> None:
        """
        Process a single group by fetching its members and creating the appropriate
        AppUserGroup and permissions. This reuses logic from _sync_user_groups.
        """
        try:
            # Get all members for this group (reused from _sync_user_groups section 3a)
            all_members = await self._fetch_group_members(group_id, group_name)

            # Create the AppUserGroup and permissions (reused from _sync_user_groups section 3b-3c)
            processor_group, member_permissions = self._create_user_group_with_permissions(
                group_id, group_name, all_members
            )

            # Send to processor (reused from _sync_user_groups section 4)
            user_groups_batch = [(processor_group, member_permissions)]

            self.logger.info(f"Submitting newly created group {group_name} to processor...")
            await self.data_entities_processor.on_new_user_groups(user_groups_batch)
            self.logger.info(f"Successfully processed group_create event for {group_name}")

        except Exception as e:
            self.logger.error(f"Failed to process single group {group_name} ({group_id}): {e}", exc_info=True)


    async def _fetch_group_members(self, group_id: str, group_name: str, *, raise_on_partial: bool = False) -> list:
        """
        Fetch all members for a group with pagination.
        Extracted from _sync_user_groups section 3a for reusability.

        With raise_on_partial, a later page that can't be read raises instead of
        ending the list early, for a caller that saves the list in place of the stored one.
        """
        all_members = []

        members_response = await self.data_source.team_groups_members_list(group=group_id)
        if not members_response.success:
            raise Exception(f"Error fetching members for group {group_name}: {members_response.error}")

        all_members.extend(members_response.data.members)
        member_cursor = members_response.data.cursor
        member_has_more = members_response.data.has_more

        while member_has_more:
            self.logger.debug(f"Fetching more members for {group_name}...")
            members_response = await self.data_source.team_groups_members_list_continue(member_cursor)

            if not members_response.success:
                if raise_on_partial:
                    raise Exception(f"Error fetching more members for group {group_name}: {members_response.error}")
                self.logger.error(f"Error during member pagination for {group_name}: {members_response.error}")
                break

            all_members.extend(members_response.data.members)
            member_cursor = members_response.data.cursor
            member_has_more = members_response.data.has_more

        return all_members

    def _create_user_group_with_permissions(self, group_id: str, group_name: str, all_members: list) -> tuple:
        """
        Create AppUserGroup and permissions list from group members.
        Extracted from _sync_user_groups sections 3b-3c for reusability.
        """
        # Permission mapping (from _sync_user_groups)


        # Create the AppUserGroup object (from _sync_user_groups section 3b)
        processor_group = AppUserGroup(
            app_name=self.connector_name,
            connector_id=self.connector_id,
            source_user_group_id=group_id,
            name=group_name,
            org_id=self.data_entities_processor.org_id
        )

        # Create permissions list (from _sync_user_groups section 3c)
        member_permissions = []
        for member in all_members:
            user_permission = AppUser(
                app_name=self.connector_name,
                connector_id=self.connector_id,
                source_user_id=member.profile.team_member_id,
                email=member.profile.email,
                full_name=member.profile.name.display_name,
            )
            member_permissions.append(user_permission)

        return processor_group, member_permissions

    async def _handle_group_renamed_event(self, event) -> None:
        """Handle group_rename events from Dropbox audit log."""
        # Extract group info from event participants
        group_id = None
        new_group_name = None

        for participant in event.participants:
            if participant.is_group():
                group_info = participant.get_group()
                group_id = group_info.group_id
                new_group_name = group_info.display_name  # This should have the updated name
                break


        details_obj = event.details

        # Log the structure for debugging
        self.logger.debug(f"Event details type: {type(details_obj)}")
        self.logger.debug(f"Event details: {details_obj}")

        old_name: Optional[str] = None
        new_name_from_details: Optional[str] = None

        # Try different ways to access the GroupRenameDetails
        if hasattr(details_obj, 'get_group_rename_details'):
            group_rename_details = details_obj.get_group_rename_details()
            old_name = group_rename_details.previous_value
            new_name_from_details = group_rename_details.new_value
            self.logger.debug(
                "Used get_group_rename_details() method: old_name=%s, new_name=%s",
                old_name,
                new_name_from_details,
            )

        new_name = new_name_from_details or new_group_name

        if not group_id or not new_name:
            self.logger.warning(
                f"Could not extract required info from group_rename event. "
                f"group_id={group_id}, new_name={new_name}"
            )
            return

        self.logger.info(f"Renaming group {group_id} from '{old_name}' to '{new_name}'")

        try:
            await self._update_group_name(group_id, new_name, old_name)

        except Exception as e:
            self.logger.error(f"Error processing group_rename event for group {group_id}: {e}", exc_info=True)

    async def _update_group_name(self, group_id: str, new_name: str, old_name: str = None) -> None:
        """Update the name of an existing group in the database."""
        success = await self.data_entities_processor.update_user_group_name(
            external_group_id=group_id,
            new_name=new_name,
            connector_id=self.connector_id,
        )
        if success:
            self.logger.info(
                f"Successfully renamed group {group_id} from '{old_name}' to '{new_name}'"
            )
        else:
            self.logger.warning(
                f"Cannot rename group: Group with external ID {group_id} not found in database"
            )

    async def _handle_group_change_member_role_event(self, event) -> None:
        """Handle group_change_member_role events from Dropbox audit log."""
        # Extract group and user info from event participants
        group_id = None
        group_name = None
        user_email = None

        for participant in event.participants:
            if participant.is_group():
                group_info = participant.get_group()
                group_id = group_info.group_id
                group_name = group_info.display_name
            elif participant.is_user():
                user_info = participant.get_user()
                user_email = user_info.email

        # Extract new role from event details
        new_is_owner = None
        try:
            if hasattr(event.details, 'get_group_change_member_role_details'):
                role_details = event.details.get_group_change_member_role_details()
                new_is_owner = role_details.is_group_owner
            elif hasattr(event.details, 'is_group_owner'):
                new_is_owner = event.details.is_group_owner
            else:
                self.logger.warning(f"Could not extract role details from event: {event.details}")
        except Exception as e:
            self.logger.warning(f"Error extracting role details: {e}")
            self.logger.debug(f"Event details: {event.details}")

        # Validate we have required information
        if not group_id or not user_email or new_is_owner is None:
            self.logger.warning(
                f"Missing required info from group_change_member_role event: "
                f"group_id={group_id}, user_email={user_email}, new_is_owner={new_is_owner}"
            )
            return

        # Convert boolean to permission type
        new_permission_type = PermissionType.OWNER if new_is_owner else PermissionType.WRITE

        self.logger.info(
            f"Changing role for user {user_email} in group '{group_name}' ({group_id}) "
            f"to {'owner' if new_is_owner else 'member'} (permission: {new_permission_type})"
        )

        try:
            success = await self._update_user_group_permission(
                group_id, user_email, new_permission_type
            )

            if success:
                self.logger.info(
                    f"Successfully updated role for {user_email} in group {group_name}"
                )
            else:
                self.logger.warning(
                    f"Failed to update role for {user_email} in group {group_name}"
                )

        except Exception as e:
            self.logger.error(
                f"Error processing group_change_member_role event for user {user_email} "
                f"in group {group_id}: {e}", exc_info=True
            )

    async def _update_user_group_permission(
        self,
        group_id: str,
        user_email: str,
        new_permission_type: PermissionType
    ) -> bool:
        """Update a user's permission level within a group."""
        try:
            user = await self.data_entities_processor.get_user_by_email(user_email)
            if not user:
                self.logger.warning(
                    f"Cannot update group permission: User with email {user_email} not found"
                )
                return False

            user_group = await self.data_entities_processor.get_user_group_by_external_id(
                connector_id=self.connector_id,
                external_id=group_id,
            )
            if not user_group:
                self.logger.warning(
                    f"Cannot update group permission: Group with external ID {group_id} not found"
                )
                return False

            # A membership is the user's grant on the group: typed USER, as access queries expect.
            permission = Permission(
                external_id=user.id,
                email=user_email,
                type=new_permission_type,
                entity_type=EntityType.USER,
            )
            await self.data_entities_processor.upsert_permission_edge(
                from_id=user.id,
                from_collection=CollectionNames.USERS.value,
                to_id=user_group.id,
                to_collection=CollectionNames.GROUPS.value,
                permission=permission,
            )
            return True

        except Exception as e:
            self.logger.error(
                f"Failed to update permission for {user_email} in group {group_id}: {e}",
                exc_info=True,
            )
            return False

    def _extract_folder_info_from_event(self, event) -> tuple[Optional[str], Optional[str]]:
        """
        Extract folder ID and name from event assets.

        Returns:
            Tuple of (folder_id, folder_name) or (None, None) if extraction fails
        """
        folder_id = None
        folder_name = None

        for asset in event.assets:
            if asset.is_folder():
                folder_info = asset.get_folder()
                folder_name = folder_info.display_name

                if (folder_info.path and
                    folder_info.path.namespace_relative and
                    folder_info.path.namespace_relative.ns_id):
                    folder_id = folder_info.path.namespace_relative.ns_id
                break

        return folder_id, folder_name

    async def _create_and_sync_single_record_group(
        self,
        folder_id: str,
        folder_name: str,
        team_admin_user
    ) -> None:
        """
        Fetch folder members and create a single record group.
        Used by both full sync and incremental event handling.

        Args:
            folder_id: The Dropbox team folder ID (ns_id)
            folder_name: The display name of the folder
            team_admin_user: AppUser with team_admin role for API calls
        """
        try:
            # Fetch folder members with pagination
            all_users_list = []
            all_groups_list = []

            folder_members = await self.data_source.sharing_list_folder_members(
                shared_folder_id=folder_id,
                team_member_id=team_admin_user.source_user_id,
                as_admin=True
            )

            if not folder_members.success:
                self.logger.warning(
                    f"Failed to fetch members for folder '{folder_name}': {folder_members.error}"
                )
                return

            # Collect members from first page
            if folder_members.data.users:
                all_users_list.extend(folder_members.data.users)
            if folder_members.data.groups:
                all_groups_list.extend(folder_members.data.groups)

            # Handle pagination
            cursor = folder_members.data.cursor
            has_more = getattr(folder_members.data, 'has_more', False)

            while has_more:
                self.logger.debug(f"Fetching more members for folder '{folder_name}'...")

                members_continue = await self.data_source.sharing_list_folder_members_continue(
                    cursor=cursor,
                    team_member_id=team_admin_user.source_user_id,
                    as_admin=True
                )

                if not members_continue.success:
                    self.logger.error(
                        f"Error during member pagination for folder '{folder_name}': {members_continue.error}"
                    )
                    break

                if members_continue.data.users:
                    all_users_list.extend(members_continue.data.users)
                if members_continue.data.groups:
                    all_groups_list.extend(members_continue.data.groups)

                cursor = members_continue.data.cursor
                has_more = getattr(members_continue.data, 'has_more', False)

            self.logger.info(
                f"Fetched {len(all_users_list)} users and {len(all_groups_list)} groups "
                f"for folder '{folder_name}'"
            )

            # Create record group
            record_group = RecordGroup(
                name=folder_name,
                org_id=self.data_entities_processor.org_id,
                external_group_id=folder_id,
                description="Team Folder",
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                group_type=RecordGroupType.DRIVE,
            )

            # Create permissions
            dropbox_to_permission_type = {
                'owner': PermissionType.OWNER,
                'editor': PermissionType.WRITE,
                'viewer': PermissionType.READ,
            }

            permissions_list = []

            for user_info in all_users_list:
                access_level_tag = user_info.access_type._tag
                permission_type = dropbox_to_permission_type.get(access_level_tag, PermissionType.READ)

                user_permission = Permission(
                    email=user_info.user.email,
                    type=permission_type,
                    entity_type=EntityType.USER
                )
                permissions_list.append(user_permission)

            for group_info in all_groups_list:
                access_level_tag = group_info.access_type._tag
                permission_type = dropbox_to_permission_type.get(access_level_tag, PermissionType.READ)

                group_permission = Permission(
                    external_id=group_info.group.group_id,
                    type=permission_type,
                    entity_type=EntityType.GROUP
                )
                permissions_list.append(group_permission)

            # Submit to processor
            await self.data_entities_processor.on_new_record_groups([(record_group, permissions_list)])
            self.logger.info(f"Successfully synced record group '{folder_name}' ({folder_id})")

        except Exception as e:
            self.logger.error(
                f"Error creating record group for folder '{folder_name}' ({folder_id}): {e}",
                exc_info=True
            )
            raise


    async def sync_personal_record_groups(self, users: List[AppUser]) -> None:
        record_groups = []
        for user in users:
            # Validate data first
            if not user.full_name or not user.full_name.strip():
                self.logger.warning(f"⚠️ Skipping user with empty full_name: {user.email}")
                continue

            if not user.source_user_id or not user.source_user_id.strip():
                self.logger.warning(f"⚠️ Skipping user with empty source_user_id: {user.email}")
                continue

            record_group = RecordGroup(
                name=user.full_name,
                org_id=self.data_entities_processor.org_id,
                description="Personal Folder",
                external_group_id=user.source_user_id,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                group_type=RecordGroupType.DRIVE,
            )

            # Create permission for the user (OWNER)
            user_permission = Permission(
                email=user.email,
                type=PermissionType.OWNER,
                entity_type=EntityType.USER
            )

            # Append the record group and its associated permissions
            record_groups.append((record_group, [user_permission]))

        await self.data_entities_processor.on_new_record_groups(record_groups)

    async def _sync_record_group_changes_with_cursor(self, users: List[AppUser]) -> None:
        """
        Syncs record group (team folder) changes incrementally using the team event log cursor.
        """
        try:
            self.logger.info("Starting incremental sync for record groups...")

            sync_point_key = generate_record_sync_point_key(
                "record_group_events", "team_events", "global"
            )

            sync_point = await self.dropbox_cursor_sync_point.read_sync_point(sync_point_key)
            cursor = sync_point.get('cursor')

            if not cursor:
                self.logger.warning("No cursor found for incremental record group sync.")
                # Initialize cursor for team folder events
                await self._initialize_event_cursor(sync_point_key, EventCategory.team_folders)
                return

            has_more = True
            latest_cursor_to_save = cursor

            while has_more:
                try:
                    async with self.rate_limiter:
                        response = await self.data_source.team_log_get_events_continue(cursor)

                    if not response.success:
                        self.logger.error(f"Error fetching team folder event log: {response.error}")
                        if is_cursor_reset(response.error):
                            await self._initialize_event_cursor(sync_point_key, EventCategory.team_folders)
                            return
                        self._health.skipped("team folder changes", response.error)
                        break

                    events = response.data.events
                    self.logger.info(f"Processing {len(events)} team folder events.")

                    for event in events:
                        try:
                            await self._process_record_group_event(event, users)
                        except Exception as e:
                            self.logger.error(f"Error processing record group event: {e}", exc_info=True)
                            continue

                    latest_cursor_to_save = response.data.cursor
                    has_more = response.data.has_more
                    cursor = latest_cursor_to_save

                except Exception as e:
                    self.logger.error(f"Error in record group sync loop: {e}", exc_info=True)
                    has_more = False

            if latest_cursor_to_save:
                await self.dropbox_cursor_sync_point.update_sync_point(
                    sync_point_key,
                    sync_point_data={"cursor": latest_cursor_to_save}
                )

        except Exception as e:
            self.logger.error(f"Fatal error in incremental record group sync: {e}", exc_info=True)
            raise

    async def _process_record_group_event(self, event, users) -> None:
        """
        Process a single record group (team folder) event from the Dropbox audit log.
        """
        try:
            self.logger.debug(f"Processing record group event: {event}")

            event_type = event.event_type._tag

            if event_type == "team_folder_create":
                await self._handle_record_group_created_event(event, users)
            elif event_type == "team_folder_rename":
                await self._handle_record_group_renamed_event(event)
            elif event_type == "team_folder_permanently_delete":
                await self._handle_record_group_deleted_event(event)
            elif event_type == "team_folder_change_status":
                await self._handle_record_group_status_changed_event(event, users)
            else:
                self.logger.debug(f"Ignoring event type: {event_type}")

        except Exception as e:
            self.logger.error(f"Error processing record group event of type {getattr(event, 'event_type', 'unknown')}: {e}", exc_info=True)

    async def _handle_record_group_created_event(self, event, users: List[AppUser]) -> None:
        """Handle team_folder_create events."""
        folder_id, folder_name = self._extract_folder_info_from_event(event)

        if not folder_id or not folder_name:
            self.logger.warning(
                f"Could not extract folder info from team_folder_create event. "
                f"folder_id={folder_id}, folder_name={folder_name}"
            )
            return

        self.logger.info(f"Creating record group for team folder '{folder_name}' (ID: {folder_id})")

        # Find team admin user
        team_admin_user = None
        for user in users:
            if user.source_role == "team_admin":
                team_admin_user = user
                break

        if not team_admin_user:
            self.logger.error("No team admin user found. Cannot sync newly created folder.")
            return

        try:
            await self._create_and_sync_single_record_group(folder_id, folder_name, team_admin_user)
            self.logger.info(f"Successfully processed team_folder_create event for '{folder_name}'")
        except Exception as e:
            self.logger.error(
                f"Error processing team_folder_create event for folder '{folder_name}' ({folder_id}): {e}",
                exc_info=True
            )

    async def _handle_record_group_renamed_event(self, event) -> None:
        """Handle team_folder_rename events."""
        folder_id, new_name = self._extract_folder_info_from_event(event)

        # Try to get old name from event details
        old_name = None
        if hasattr(event.details, 'get_team_folder_rename_details'):
            rename_details = event.details.get_team_folder_rename_details()
            old_name = getattr(rename_details, 'previous_folder_name', None)

        if not folder_id or not new_name:
            self.logger.warning(
                f"Could not extract required info from team_folder_rename event. "
                f"folder_id={folder_id}, new_name={new_name}"
            )
            return

        self.logger.info(f"Renaming record group {folder_id} from '{old_name}' to '{new_name}'")

        try:
            await self.data_entities_processor.update_record_group_name(folder_id, new_name, old_name, self.connector_id)
        except Exception as e:
            self.logger.error(
                f"Error processing team_folder_rename event for folder {folder_id}: {e}",
                exc_info=True
            )

    async def _handle_record_group_deleted_event(self, event) -> None:
        """Handle team_folder_permanently_delete events."""
        folder_id, folder_name = self._extract_folder_info_from_event(event)

        if not folder_id:
            self.logger.warning("Could not extract folder_id from team_folder_permanently_delete event")
            return

        self.logger.info(f"Deleting record group '{folder_name}' ({folder_id})")

        try:
            # Team folder events run before the drive sync reaches the folder's files.
            await self.data_entities_processor.on_record_group_deleted(
                external_group_id=folder_id,
                connector_id=self.connector_id,
                trash_live_records=True,
            )
        except Exception as e:
            self.logger.error(
                f"Error processing team_folder_permanently_delete event for folder {folder_id}: {e}",
                exc_info=True
            )

    async def _handle_record_group_status_changed_event(self, event, users: List[AppUser]) -> None:
        """Handle team_folder_change_status events."""

        folder_id, folder_name = self._extract_folder_info_from_event(event)

        # Extract old and new status from event details
        old_status = None
        new_status = None

        if hasattr(event.details, 'get_team_folder_change_status_details'):
            status_details = event.details.get_team_folder_change_status_details()
            old_status = getattr(status_details.previous_value, '_tag', None)
            new_status = getattr(status_details.new_value, '_tag', None)

        if not folder_id or not new_status:
            self.logger.warning(
                f"Could not extract required info from team_folder_change_status event. "
                f"folder_id={folder_id}, old_status={old_status}, new_status={new_status}"
            )
            return

        self.logger.info(
            f"Status change for record group '{folder_name}' ({folder_id}): "
            f"{old_status} -> {new_status}"
        )

        try:
            # If changing TO active from any other status, treat as creation/reactivation
            if new_status == 'active':
                self.logger.info(
                    f"Folder '{folder_name}' is now active. Syncing as new/reactivated record group."
                )

                await self._handle_record_group_created_event(event, users)

            # If changing FROM active to any other status (archived, etc.), treat as deletion
            elif old_status == 'active':
                self.logger.info(
                    f"Folder '{folder_name}' is no longer active. Deleting record group."
                )
                await self._handle_record_group_deleted_event(event)

            # For other status transitions (e.g., archived -> permanently_deleted), log but don't act
            else:
                self.logger.info(
                    f"Status change from {old_status} to {new_status} for '{folder_name}'. "
                    f"No action needed (folder was already inactive)."
                )

        except Exception as e:
            self.logger.error(
                f"Error processing team_folder_change_status event for folder {folder_id}: {e}",
                exc_info=True
            )

    async def _sync_sharing_changes_with_cursor(self, sync_point_key: str, cursor: str) -> None:
        """
        Listens for sharing events and triggers the re-sync workflow using the correct
        response structure.
        """
        self.logger.info("Starting incremental sync for sharing events...")
        has_more = True
        latest_cursor_to_save = cursor

        restarted = False
        while has_more:
            try:
                response = await self.data_source.team_log_get_events_continue(cursor)

                if not response.success and is_cursor_reset(response.error) and not restarted:
                    # GetTeamEventsContinueError.reset carries the time of the last event the cursor returned:
                    # read again from there (team_log.stone), and process that page too. Re-reading an item
                    # twice is harmless; dropping the page would lose the changes in it.
                    self.logger.warning(f"Dropbox reset the sharing event cursor: {response.error}")
                    restarted = True
                    restart = _reset_time(response.error)
                    if restart is None:
                        self._health.skipped("sharing changes", "the activity log cursor was reset")
                        await self._initialize_event_cursor(sync_point_key, EventCategory.sharing)
                        return
                    from dropbox.team_common import TimeRange

                    response = await self.data_source.team_log_get_events(
                        category=EventCategory.sharing, time=TimeRange(start_time=restart),
                    )

                if not response.success:
                    self.logger.error(f"⚠️ Error fetching sharing event log: {response.error}")
                    self._health.skipped("sharing changes", response.error)
                    break

                for event in response.data.events:
                    try:
                        event_type = event.event_type._tag

                        # 1. Check for the correct, specific event types
                        if event_type in SHARING_CHANGE_EVENTS:
                            self.logger.info(f"Processing permission change event: {event_type}")

                            member = self._sharing_event_member(event)
                            if member is None:
                                self.logger.warning(f"{event_type} names no team member to read the item as. Skipping.")
                                continue
                            team_member_id, user_email = member

                            # 3. Iterate through assets to find the file/folder ID (CORRECTED LOGIC)
                            if not event.assets:
                                self.logger.warning(f"Event {event_type} has no assets. Skipping.")
                                continue

                            for asset in event.assets:

                                if asset.is_file():
                                    file_info = asset.get_file()
                                    id_to_sync = file_info.file_id
                                    ns_id_for_file = None
                                    if file_info.path and file_info.path.namespace_relative:
                                        ns_id_for_file = file_info.path.namespace_relative.ns_id

                                    await self._resync_record_by_external_id(
                                        external_id=id_to_sync,
                                        team_member_id=team_member_id,
                                        user_email=user_email,
                                        ns_id=ns_id_for_file
                                    )

                                elif asset.is_folder():
                                    # A shared folder is a record group: re-read its name, place and members.
                                    folder_info = asset.get_folder()
                                    if folder_info.path and folder_info.path.namespace_relative:
                                        await self._refresh_shared_folder(
                                            folder_info.path.namespace_relative.ns_id, team_member_id,
                                        )

                    except Exception as e:
                        self.logger.error(f"Error processing a single sharing event: {e}", exc_info=True)
                        continue # Move to the next event

                # 5. Update state for the next loop
                latest_cursor_to_save = response.data.cursor
                has_more = response.data.has_more
                cursor = latest_cursor_to_save

            except Exception as e:
                self.logger.error(f"Fatal error in sharing sync loop: {e}", exc_info=True)
                has_more = False

        # 6. Save the final cursor
        self.logger.info(f"Storing latest sharing sync cursor for key {sync_point_key}")
        await self.dropbox_cursor_sync_point.update_sync_point(
            sync_point_key,
            sync_point_data={"cursor": latest_cursor_to_save}
        )

    @staticmethod
    def _sharing_event_member(event: TeamEvent) -> tuple[str, str] | None:
        """The team member to re-read a shared item as: whose content it is (the event's context).

        The actor is only a fallback. A change made through an app has an app
        actor, which is no member, and the actor need not have access anyway.
        """
        context = event.context
        if context is not None and context.is_team_member():
            info = context.get_team_member()
            return info.team_member_id, info.email
        actor = event.actor
        for is_kind, get in ((actor.is_user, actor.get_user), (actor.is_admin, actor.get_admin)):
            if is_kind():
                info = get()
                if getattr(info, "team_member_id", None):
                    return info.team_member_id, info.email
        return None

    async def _resync_record_by_external_id(
        self,
        external_id: str,
        team_member_id: str,
        user_email: str,
        ns_id: Optional[str] = None
    ) -> None:
        """Re-read one file after a sharing event on it and apply what changed."""
        self.logger.info(f"Re-syncing file due to a permission event: {external_id}")
        try:
            metadata_result = await self.data_source.files_get_metadata(
                path=external_id,
                team_member_id=team_member_id,
                team_folder_id=ns_id
            )
            if not metadata_result or not metadata_result.success:
                self.logger.error(f"Could not fetch metadata for re-sync: {external_id}. Error: {metadata_result.error}")
                return

            existing_record = await self.data_entities_processor.get_record_by_external_id(self.connector_id, external_id)
            if not existing_record:
                self.logger.warning(f"File record {external_id} not found in DB for re-sync. Cannot determine parent group.")
                return
            record_group_id = existing_record.external_record_group_id

            # A sharing change does not show in the file's metadata, so its members are read whatever the
            # listing flag says.
            record_update = await self._process_dropbox_entry(
                entry=metadata_result.data,
                user_id=team_member_id,
                user_email=user_email,
                record_group_id=record_group_id,
                is_person_folder=bool(record_group_id) and record_group_id.startswith("dbmid:"),
                force_members=True,
            )

            if record_update and record_update.record:
                self.logger.info(f"Permissions updated for {record_update.record.record_name}")
                await self._handle_record_updates(record_update)
            else:
                self.logger.info(f"Re-sync for {external_id} did not result in an update.")

        except Exception as e:
            self.logger.error(f"Error during single record re-sync for {external_id}: {e}", exc_info=True)

    async def run_incremental_sync(self) -> None:
        """Every run is incremental already: each folder is listed from its cursor."""
        await self.run_sync()

    async def get_signed_url(self, record: Record) -> Optional[str]:
        if not self.data_source:
            raise connector_not_ready(self.display_name)
        try:
            file_record = await self.data_entities_processor.get_file_record_by_id(record.id)
            if not file_record:
                self.logger.warning(f"No file record found for node: {record.id}")
                raise not_downloadable(
                    "This item is missing the file metadata needed to download it.",
                    connector=self.display_name,
                )
            members = await self._members_with_access(record, _MAX_STREAM_CANDIDATES)
            if not members:
                self.logger.warning(f"No user found with permission to node: {record.id}")
                raise not_downloadable(
                    "PipesHub has no user with access to this item, so it cannot be "
                    "downloaded.",
                    connector=self.display_name,
                )
            # A path in a shared namespace is relative to that namespace's root.
            team_folder_id = None
            if record.external_record_group_id and not record.external_record_group_id.startswith("dbmid:"):
                team_folder_id = record.external_record_group_id

            last_error: ApiError | None = None
            for team_member_id in members:
                try:
                    response = await self.data_source.files_get_temporary_link(
                        path=file_record.path,
                        team_folder_id=team_folder_id,
                        team_member_id=team_member_id,
                        raise_on_error=True,
                    )
                except ApiError as e:
                    # That member cannot open it (moved out of their view, access removed): ask the next.
                    self.logger.info(f"Member {team_member_id} could not open record {record.id}: {e}")
                    last_error = e
                    continue
                raise_for_stream_fetch(
                    success=response.success,
                    has_payload=bool(response.data),
                    connector=self.display_name,
                    message=response.error,
                )
                return response.data.link
            raise last_error
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(
                f"Error creating signed URL for record {record.id}: {e}", exc_info=True
            )
            raise to_stream_error(e, connector=self.display_name) from e


    async def stream_record(self, record: Record) -> StreamingResponse:
        signed_url = await self.get_signed_url(record)
        if not signed_url:
            raise not_found_at_source(self.display_name)

        return create_stream_record_response(
            stream_content(
                signed_url,
                record_id=record.id,
                file_name=record.record_name,
                connector=self.display_name,
            ),
            filename=record.record_name,
            mime_type=record.mime_type,
            fallback_filename=f"record_{record.id}"
        )

    async def test_connection_and_access(self) -> bool:
        """Whether Dropbox accepts the team token. users/get_current_account is not a team route (a team token
        needs a member to act as), so the admin the token was linked by is asked for instead."""
        if not self.data_source:
            return False
        try:
            response = await self.data_source.team_token_get_authenticated_admin()
        except Exception as e:
            self.logger.error(f"Dropbox connection test failed: {e}", exc_info=True)
            return False
        if not response.success:
            self.logger.error(f"Dropbox connection test failed: {response.error}")
            return False
        self.logger.info("Dropbox connection test successful.")
        return True

    def handle_webhook_notification(self, notification: Dict) -> None:
        """Handles a webhook notification by triggering an incremental sync."""
        self.logger.info("Dropbox webhook received. Triggering incremental sync.")
        asyncio.create_task(self.run_incremental_sync())

    async def cleanup(self) -> None:
        self.logger.info("Cleaning up Dropbox connector resources.")
        self.data_source = None

    async def reindex_records(self, records: List[Record]) -> None:
        """
        Reindex records from Dropbox.

        This method checks each record at the source for updates:
        - If the record has changed (metadata, content, or permissions), it updates the DB
        - If the record hasn't changed, it publishes a reindex event for the existing record
        """
        try:
            if not records:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(records)} Dropbox records")

            # Ensure Dropbox client is initialized
            if not self.data_source:
                self.logger.error("Dropbox client not initialized. Call init() first.")
                raise Exception("Dropbox client not initialized. Call init() first.")

            # Check records at source for updates
            org_id = self.data_entities_processor.org_id
            updated: list[RecordUpdate] = []
            non_updated_records = []

            for record in records:
                try:
                    update = await self._check_and_fetch_updated_record(org_id, record)
                    if update is not None:
                        updated.append(update)
                    if update is None or not update.content_changed:
                        # Unchanged content raises no index event when written, so it is re-indexed here.
                        non_updated_records.append(record)
                except Exception as e:
                    self.logger.error(f"Error checking record {record.id} at source: {e}")
                    continue

            # Write records that changed at source; their content events re-index them.
            if updated:
                await self._write_updates(updated, "reindex")
                # A folder that moved is stored at its new path now, so no later listing sees the move: the
                # paths below it are rewritten here.
                await self._repath_moved_folders()
                self.logger.info(f"Updated {len(updated)} records in DB that changed at source")

            # Publish reindex events for non-updated records
            if non_updated_records:
                await self.data_entities_processor.reindex_existing_records(non_updated_records)
                self.logger.info(f"Published reindex events for {len(non_updated_records)} non-updated records")

        except Exception as e:
            self.logger.error(f"Error during Dropbox reindex: {e}", exc_info=True)
            raise

    async def _check_and_fetch_updated_record(
        self, org_id: str, record: Record
    ) -> RecordUpdate | None:
        """
        Fetch record from Dropbox and return data for reindexing if changed.

        Args:
            org_id: The organization ID
            record: The record to check for updates

        Returns:
            The update to write if the record has changed, None otherwise
        """
        try:
            external_id = record.external_record_id
            record_group_id = record.external_record_group_id

            if not external_id:
                self.logger.warning(f"Missing external_record_id for record {record.id}")
                return None

            # Get file record for additional info (path, etc.)
            file_record = await self.data_entities_processor.get_file_record_by_id(record.id)

            if not file_record:
                self.logger.warning(f"No file record found for record {record.id}")
                return None

            # Determine if this is a personal folder
            is_person_folder = record_group_id and record_group_id.startswith("dbmid:")

            # Determine team_folder_id for API call
            team_folder_id = None
            if record_group_id and not record_group_id.startswith("dbmid:"):
                team_folder_id = record_group_id

            # Read it as the first member who can still open it (by id, which survives moves).
            metadata_result, team_member_id = None, None
            for candidate in await self._members_with_access(record, _MAX_STREAM_CANDIDATES):
                metadata_result = await self.data_source.files_get_metadata(
                    path=external_id,
                    team_member_id=candidate,
                    team_folder_id=team_folder_id
                )
                if metadata_result and metadata_result.success:
                    team_member_id = candidate
                    break

            if team_member_id is None:
                self.logger.warning(
                    f"No member could read record {record.id}: "
                    f"{metadata_result.error if metadata_result else 'no member with access'}"
                )
                return None
            user_email = ""

            entry = metadata_result.data

            # Check if deleted
            if isinstance(entry, DeletedMetadata):
                self.logger.info(f"Record {record.id} has been deleted at source")
                return None

            # Written even when name, revision and place are unchanged: its members may have changed alone.
            record_update = await self._process_dropbox_entry(
                entry=entry,
                user_id=team_member_id,
                user_email=user_email,
                record_group_id=record_group_id,
                is_person_folder=is_person_folder,
                force_members=True,
            )
            if not record_update or not record_update.record:
                return None
            self.logger.info(f"Record {external_id} has changed at source. Updating.")
            record_update.record.id = record.id
            return record_update

        except Exception as e:
            self.logger.error(f"Error checking Dropbox record {record.id} at source: {e}", exc_info=True)
            return None

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> NoReturn:
        """Dropbox connector does not support dynamic filter options."""
        raise NotImplementedError("Dropbox connector does not support dynamic filter options")

    @classmethod
    async def create_connector(
        cls,
        logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
        data_entities_processor,
        **kwargs,
    ) -> "BaseConnector":
        return cls(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
