"""Folder-scope filter primitives shared by the personal and workspace Drive connectors.

The `folder_ids` sync filter restricts a sync to selected folder subtrees. Resolving
that scope needs the same four source walks in both connectors, which differ only in
how they obtain a live datasource (the personal connector must refresh its OAuth
credentials first, the workspace connector holds a per-user impersonated client) and
in which `fields` mask they request. Both are parameters here.
"""

import asyncio
from logging import Logger
from typing import (
    AsyncGenerator,
    Awaitable,
    Callable,
    Dict,
    List,
    NamedTuple,
    Optional,
    Tuple,
)

from googleapiclient.errors import HttpError

from app.config.constants.arangodb import MimeTypes
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.sources.google.common.drive_file_fields import (
    DRIVE_FOLDER_EXPANSION_GET_FIELDS,
    DRIVE_FOLDER_EXPANSION_LIST_FIELDS,
)
from app.models.entities import Record
from app.sources.external.google.drive.drive import GoogleDriveDataSource

# Resolved per call rather than held, so the personal connector can refresh its OAuth
# credentials between requests without this module knowing about credentials at all.
DriveDataSourceProvider = Callable[[], Awaitable[GoogleDriveDataSource]]

# Folder ids per 'in parents' OR-clause, so a deep/wide tree doesn't cost one API call
# per folder. Drive rejects very long queries, hence the modest batch.
FOLDER_QUERY_BATCH_SIZE = 15
FOLDER_LIST_PAGE_SIZE = 1000
# Bound the per-level source fanout; Drive quota is per-user.
ANCESTOR_FETCH_CONCURRENCY = 5
# Runaway backstop only — the sweep's `visited` set already guarantees termination
# (even on cyclic source data). If a sweep ever exceeds this many distinct ancestors,
# something is wrong.
PLACEHOLDER_SWEEP_SAFETY_MAX = 10000

# 403 reasons that mean this user genuinely may not see the item. Anything else,
# including a 403 with no reason or one not listed here, must not be read as
# "invisible": that would drop a subtree from scope while the checkpoint advances.
PERMISSION_DENIED_403_REASONS = {
    "insufficientFilePermissions",
    "appNotAuthorizedToFile",
    "domainPolicy",
    "teamDriveMembershipRequired",
}


# 403 reasons Drive uses for quota and rate limits. These clear as the quota refills,
# so they are retried on every run and never count toward MAX_UNRECOGNISED_403_RUNS.
QUOTA_403_REASONS = {
    "dailyLimitExceeded",
    "dailyLimitExceededUnreg",
    "quotaExceeded",
    "rateLimitExceeded",
    "sharingRateLimitExceeded",
    "userRateLimitExceeded",
}

# Runs in a row one folder may fail on an unrecognised 403 before it is skipped, so a
# folder Drive keeps refusing without a reason we recognise cannot fail every run for good.
MAX_UNRECOGNISED_403_RUNS = 5

# Sync-point fields for those runs: selected folders the folder filter could not
# resolve, and shared folders a full sync could not walk (then the ones it gave up on).
HELD_FILTER_FOLDERS = "heldFilterFolders"
HELD_SHARED_FOLDERS = "heldSharedFolders"
SKIPPED_SHARED_FOLDERS = "skippedSharedFolders"


def _403_reasons(error: HttpError) -> set:
    if error.resp.status != HttpStatusCode.FORBIDDEN.value:
        return set()
    error_details = getattr(error, "error_details", None) or []
    if not isinstance(error_details, list):
        return set()
    return {d.get("reason") for d in error_details if isinstance(d, dict)}


def is_retryable_403(error: HttpError) -> bool:
    """True for any 403 that is not a known, permanent permission refusal.

    Drive reports quota and rate limits as 403 too, and callers skip a folder for good
    when this is False, so the default must be "retry": quota and rate-limit reasons, a
    403 with no reason, details that are not a list of reasons, and reasons Drive adds
    later are all retryable.
    """
    if error.resp.status != HttpStatusCode.FORBIDDEN.value:
        return False
    return not is_permission_denied_403(error)


def is_permission_denied_403(error: HttpError) -> bool:
    """True only for a 403 whose every reported reason is a known, permanent denial.

    Google can report several reasons at once; a refusal alongside a quota or
    unknown reason is not permanent, so it must be retried rather than skipped.
    """
    reasons = _403_reasons(error)
    return bool(reasons) and reasons <= PERMISSION_DENIED_403_REASONS


def is_unrecognised_403(error: HttpError) -> bool:
    """True for a 403 that is neither a known permission refusal nor a quota or rate limit."""
    if error.resp.status != HttpStatusCode.FORBIDDEN.value:
        return False
    if _403_reasons(error) & QUOTA_403_REASONS:
        return False
    return not is_permission_denied_403(error)


class FolderFailureRuns:
    """How many runs in a row each folder has failed on an unrecognised 403.

    Kept in a sync point as a list of "folderId:runs" strings. Sync-point writes merge:
    Arango merges a nested object key by key and Neo4j cannot store one at all, while a
    list is replaced whole on both, so writing the list always states the full set.
    Drive ids never contain ":".
    """

    def __init__(self, stored: object = None) -> None:
        self._runs: dict[str, int] = {}
        for entry in stored if isinstance(stored, list) else []:
            folder_id, _, runs = str(entry).rpartition(":")
            if folder_id and runs.isdigit():
                self._runs[folder_id] = int(runs)
        self._loaded = self.to_stored()

    def runs(self, folder_id: str) -> int:
        return self._runs.get(folder_id, 0)

    def record_failure(self, folder_id: str) -> int:
        """Count this run's failure and return the runs so far, capped at the limit."""
        runs = min(self.runs(folder_id) + 1, MAX_UNRECOGNISED_403_RUNS)
        self._runs[folder_id] = runs
        return runs

    def clear(self, folder_id: str) -> None:
        self._runs.pop(folder_id, None)

    def keep_only(self, folder_ids: set) -> None:
        self._runs = {f: n for f, n in self._runs.items() if f in folder_ids}

    def folder_ids(self) -> set:
        return set(self._runs)

    @property
    def changed(self) -> bool:
        return self.to_stored() != self._loaded

    def to_stored(self) -> list[str]:
        return [f"{folder_id}:{runs}" for folder_id, runs in sorted(self._runs.items())]


class SharedFolderWalkHolds:
    """Shared folders whose walk keeps failing on an unrecognised 403, for one full sync.

    Every such folder in a walk is counted, and the walk fails at the end if any is
    still under MAX_UNRECOGNISED_403_RUNS, so folders refused together use their runs
    together. A folder at the limit keeps that count until the checkpoint is saved, so
    a run that fails on another folder does not start it over; it is then skipped and
    listed under SKIPPED_SHARED_FOLDERS.
    """

    def __init__(self, stored: dict | None = None) -> None:
        stored = stored if isinstance(stored, dict) else {}
        self.runs = FolderFailureRuns(stored.get(HELD_SHARED_FOLDERS))
        skipped = stored.get(SKIPPED_SHARED_FOLDERS)
        self.skipped: set = {str(f) for f in skipped} if isinstance(skipped, list) else set()
        self._skipped_loaded = sorted(self.skipped)
        self.retry_error: Exception | None = None

    def walked(self, folder_id: str) -> None:
        self.runs.clear(folder_id)
        self.skipped.discard(folder_id)

    def give_up(self, folder_id: str, error: Exception) -> bool:
        """Count this run's failure; True once the folder has used every run and is skipped.

        Under the limit, the error is kept for ``raise_if_retrying`` so the rest of the
        walk still runs and counts its own failures this run.
        """
        if self.runs.record_failure(folder_id) < MAX_UNRECOGNISED_403_RUNS:
            self.retry_error = self.retry_error or error
            return False
        self.skipped.add(folder_id)
        return True

    def raise_if_retrying(self) -> None:
        if self.retry_error is not None:
            raise self.retry_error

    def checkpoint_changes(self) -> dict:
        """The fields to write with the saved checkpoint.

        Every count is cleared there, so a later full sync gives each folder a fresh set
        of runs; SKIPPED_SHARED_FOLDERS keeps the record of what this one left out.
        """
        self.runs.keep_only(set())
        return self.changes()

    def changes(self) -> dict:
        """The sync-point fields to write, written whole so a merge cannot keep stale entries."""
        changes: dict = {}
        if self.runs.changed:
            changes[HELD_SHARED_FOLDERS] = self.runs.to_stored()
        if sorted(self.skipped) != self._skipped_loaded:
            changes[SKIPPED_SHARED_FOLDERS] = sorted(self.skipped)
        return changes


class FolderScopeExpansion(NamedTuple):
    """Result of one downward walk of the folder filter's subtrees."""

    tracked: set  # folders discovered in scope, blocked ones included
    expanded: set  # folders whose children were listed on this walk
    blocked: set  # folders in scope whose children this user could not list


class FolderListProbe(NamedTuple):
    """Result of probing whether a user can list a folder's children."""

    can_list_children: bool
    drive_id: Optional[str] = None  # set when the folder lives on a shared drive


def static_data_source_provider(
    data_source: GoogleDriveDataSource,
) -> DriveDataSourceProvider:
    """Adapt an already-built datasource to the provider protocol."""

    async def provide() -> GoogleDriveDataSource:
        return data_source

    return provide


def _escape_drive_q_value(value: str) -> str:
    """Escape a value for use inside a single-quoted Drive `q` string literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _parents_clause(folder_ids: List[str]) -> str:
    return " or ".join(
        f"'{_escape_drive_q_value(folder_id)}' in parents" for folder_id in folder_ids
    )


def _child_list_params(
    query: str,
    fields: str,
    *,
    drive_id: Optional[str] = None,
    page_token: Optional[str] = None,
) -> dict:
    """
    Build files.list params for a parent-query.

    Match 880fb5c for My Drive / shared-with-me folders: omit corpora (defaults to
    user) with supportsAllDrives + includeItemsFromAllDrives. Shared-drive parents
    need corpora=drive + driveId — corpora=allDrives can return incompleteSearch
    and silently drop My Drive children such as restricted subfolders.
    """
    list_params: dict = {
        "q": query,
        "fields": fields,
        "pageSize": FOLDER_LIST_PAGE_SIZE,
        "supportsAllDrives": True,
        "includeItemsFromAllDrives": True,
    }
    if drive_id:
        list_params["corpora"] = "drive"
        list_params["driveId"] = drive_id
    if page_token:
        list_params["pageToken"] = page_token
    return list_params


def _take_same_drive_batch(
    queue: List[str], drive_id_by_folder: Dict[str, Optional[str]]
) -> Tuple[List[str], List[str], Optional[str]]:
    """Pop a same-driveId batch from the front of the queue for one files.list call."""
    if not queue:
        return [], [], None
    drive_id = drive_id_by_folder.get(queue[0])
    batch: List[str] = []
    rest: List[str] = []
    for folder_id in queue:
        if len(batch) < FOLDER_QUERY_BATCH_SIZE and drive_id_by_folder.get(folder_id) == drive_id:
            batch.append(folder_id)
        else:
            rest.append(folder_id)
    return batch, rest, drive_id


def pass_folder_filter(metadata: dict, tracked_folder_ids: Optional[set]) -> bool:
    """
    Checks if the Google Drive item is inside the configured folder scope.

    Unlike the date/extension filters, folders themselves are NOT always
    allowed through: if a folder is outside the tracked subtree, it (and
    everything under it) must be skipped so only the selected subtree syncs.
    """
    if not tracked_folder_ids:
        return True

    file_id = metadata.get("id")
    if file_id and file_id in tracked_folder_ids:
        return True

    parents = metadata.get("parents") or []
    return any(parent_id in tracked_folder_ids for parent_id in parents)


async def _record_and_parent_in_scope(
    data_entities_processor: DataSourceEntitiesProcessor,
    connector_id: str,
    file_id: str,
    tracked_folder_ids: set,
) -> Tuple[Optional[Record], bool]:
    """Load the persisted record for `file_id` and say whether its parent is in scope."""
    existing_record = await data_entities_processor.get_record_by_external_id(
        connector_id=connector_id,
        external_record_id=file_id,
    )

    if existing_record is None:
        return None, False

    return (
        existing_record,
        existing_record.parent_external_record_id in tracked_folder_ids,
    )


async def has_entered_scope(
    data_entities_processor: DataSourceEntitiesProcessor,
    connector_id: str,
    file_id: str,
    tracked_folder_ids: set,
) -> bool:
    """
    Checks whether an item that already passed `pass_folder_filter` just
    moved from outside the tracked folder scope to inside it (or is brand
    new inside scope). Such items need their descendants pulled in
    recursively, since changes_list only reports the item itself, not its
    children.

    Only meaningful when `tracked_folder_ids` is set; callers should not
    invoke this otherwise.
    """
    existing_record, parent_in_scope = await _record_and_parent_in_scope(
        data_entities_processor, connector_id, file_id, tracked_folder_ids
    )

    if existing_record is None:
        return True

    return not parent_in_scope


async def has_exited_scope(
    data_entities_processor: DataSourceEntitiesProcessor,
    connector_id: str,
    file_id: str,
    tracked_folder_ids: set,
) -> Tuple[bool, Optional[Record]]:
    """
    Checks whether an item that just failed `pass_folder_filter` was
    previously inside the tracked folder scope (i.e. it moved out, rather
    than never having been in scope under the current filter). If so, its
    previously-synced record is now stale and must be deleted.

    Only meaningful when `tracked_folder_ids` is set; callers should not
    invoke this otherwise.
    """
    existing_record, parent_in_scope = await _record_and_parent_in_scope(
        data_entities_processor, connector_id, file_id, tracked_folder_ids
    )

    if existing_record is None:
        return False, None

    return parent_in_scope, existing_record


async def probe_can_list_children(
    folder_id: str,
    get_data_source: DriveDataSourceProvider,
    logger: Logger,
) -> Optional[FolderListProbe]:
    """
    Ask whether the impersonated user may enumerate this folder's children.

    Returns None when the folder is invisible to this user, which files_list
    cannot distinguish from an empty folder — both come back with zero children.
    On success also returns driveId when the folder lives on a shared drive.

    Only a 404 or a 403 with a known permission-denial reason means invisible.
    Any other failure (a quota or rate-limit 403, a 403 with an unknown reason, a
    5xx, a network error) is raised: reading it as "invisible" would drop the
    folder's subtree from this run's scope while the sync still saves its
    checkpoint, so files under it would be skipped for good.
    """
    try:
        data_source = await get_data_source()
        response = await data_source.files_get(
            fileId=folder_id,
            fields=DRIVE_FOLDER_EXPANSION_GET_FIELDS,
            supportsAllDrives=True,
        )
    except HttpError as e:
        status = e.resp.status
        if status == HttpStatusCode.NOT_FOUND.value or is_permission_denied_403(e):
            logger.debug(
                f"Folder {folder_id} is not visible to this user (HTTP {status})"
            )
            return None
        logger.warning(f"Failed to probe folder {folder_id} (HTTP {status}): {e}")
        raise

    response = response or {}
    return FolderListProbe(
        can_list_children=bool(
            (response.get("capabilities") or {}).get("canListChildren")
        ),
        drive_id=response.get("driveId") or None,
    )


async def build_tracked_folder_ids(
    frontier_folder_ids: List[str],
    get_data_source: DriveDataSourceProvider,
    logger: Logger,
    *,
    already_expanded: Optional[set] = None,
    drive_id_by_folder: Optional[Dict[str, Optional[str]]] = None,
) -> FolderScopeExpansion:
    """
    Walk down from the given folder IDs, collecting every descendant subfolder the
    caller can reach.

    A subfolder the caller can see but not list is recorded in `blocked` instead of
    being descended into: listing it would return zero children and wrongly bank the
    subtree below it as empty. `already_expanded` names folders some earlier walk
    already descended into, so a multi-user caller can skip re-walking them.
    """
    expanded_elsewhere = already_expanded or set()
    tracked: set = set(frontier_folder_ids)
    expanded: set = set()
    blocked: set = set()
    queue: List[str] = list(frontier_folder_ids)
    folder_mime = MimeTypes.GOOGLE_DRIVE_FOLDER.value
    drive_ids: Dict[str, Optional[str]] = {
        folder_id: (drive_id_by_folder or {}).get(folder_id)
        for folder_id in frontier_folder_ids
    }

    while queue:
        batch, queue, drive_id = _take_same_drive_batch(queue, drive_ids)
        query = (
            f"mimeType='{folder_mime}' and trashed=false and ({_parents_clause(batch)})"
        )

        page_token = None
        while True:
            list_params = _child_list_params(
                query,
                DRIVE_FOLDER_EXPANSION_LIST_FIELDS,
                drive_id=drive_id,
                page_token=page_token,
            )

            data_source = await get_data_source()
            response = await data_source.files_list(**list_params) or {}

            for subfolder in response.get("files", []):
                subfolder_id = subfolder.get("id")
                if not subfolder_id or subfolder_id in tracked:
                    continue

                tracked.add(subfolder_id)
                # Children of a shared-drive folder stay on that drive.
                drive_ids[subfolder_id] = drive_id

                if subfolder_id in expanded_elsewhere:
                    continue

                if (subfolder.get("capabilities") or {}).get("canListChildren"):
                    queue.append(subfolder_id)
                else:
                    blocked.add(subfolder_id)
                    logger.debug(
                        f"Folder {subfolder_id} cannot be listed by this user; "
                        "deferring its subtree"
                    )

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        expanded |= set(batch)

    return FolderScopeExpansion(tracked=tracked, expanded=expanded, blocked=blocked)


async def fetch_folder_children(
    folder_id: str,
    changes_ids: set,
    get_data_source: DriveDataSourceProvider,
    *,
    fields: str,
    drive_scoped: bool = True,
) -> AsyncGenerator[List[dict], None]:
    """
    Recursively fetch all descendants (files and folders) of a folder that
    just entered the tracked scope, yielding them in batches.

    Unlike `build_tracked_folder_ids`, this fetches every child item, not
    just folders, but only recurses into children that are themselves
    folders. Children already present in `changes_ids` (the current
    changes_list page) are skipped so they aren't processed twice: they
    are already flowing through the regular changes loop.

    `drive_scoped=False` skips resolving driveId, leaving the query on the default
    user corpus. Callers walking a shared drive they are not a member of need that:
    corpora=drive requires membership, while the user corpus still resolves children
    of a folder the caller holds an individual grant on.
    """
    # Resolve driveId once so shared-drive subtrees use corpora=drive.
    root_drive_id: Optional[str] = None
    if drive_scoped:
        try:
            data_source = await get_data_source()
            probe = await data_source.files_get(
                fileId=folder_id,
                fields="driveId",
                supportsAllDrives=True,
            )
            root_drive_id = (probe or {}).get("driveId") or None
        except Exception:
            root_drive_id = None

    queue: List[str] = [folder_id]
    drive_ids: Dict[str, Optional[str]] = {folder_id: root_drive_id}
    folder_mime = MimeTypes.GOOGLE_DRIVE_FOLDER.value

    while queue:
        batch, queue, drive_id = _take_same_drive_batch(queue, drive_ids)
        query = f"trashed=false and ({_parents_clause(batch)})"

        page_token = None
        while True:
            list_params = _child_list_params(
                query, fields, drive_id=drive_id, page_token=page_token
            )

            data_source = await get_data_source()
            response = await data_source.files_list(**list_params) or {}

            children_batch = []
            for child in response.get("files", []):
                child_id = child.get("id")
                if not child_id or child_id in changes_ids:
                    continue
                if child.get("mimeType") == folder_mime:
                    queue.append(child_id)
                    drive_ids[child_id] = drive_id
                children_batch.append(child)

            if children_batch:
                yield children_batch

            page_token = response.get("nextPageToken")
            if not page_token:
                break


async def fetch_ancestor_metadata(
    frontier: List[Record],
    get_data_source: DriveDataSourceProvider,
    logger: Logger,
    *,
    fields: str,
    concurrency: int = ANCESTOR_FETCH_CONCURRENCY,
) -> Dict[str, dict]:
    """Fetch one placeholder-sweep frontier level from source with bounded concurrency.

    Returns metadata keyed by external record id. An id missing from the result
    could not be read: the folder was deleted, or it is the parent of a
    shared-with-me item that this user has no access to.
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def fetch_one(stub: Record) -> Optional[Tuple[str, dict]]:
        async with semaphore:
            file_id = stub.external_record_id
            try:
                data_source = await get_data_source()
                metadata = await data_source.files_get(
                    fileId=file_id,
                    supportsAllDrives=True,
                    fields=fields,
                )
                if not metadata:
                    logger.warning(
                        f"Placeholder sweep: no metadata returned for {file_id}"
                    )
                    return None
                return (file_id, metadata)
            except HttpError as e:
                logger.warning(
                    f"Placeholder sweep: cannot read ancestor {file_id} "
                    f"(HTTP {e.resp.status}); keeping it as a stub"
                )
                return None
            except Exception as e:
                logger.warning(
                    f"Placeholder sweep: failed to fetch ancestor {file_id}: {e}"
                )
                return None

    results = await asyncio.gather(*[fetch_one(stub) for stub in frontier])
    return dict(r for r in results if r)
