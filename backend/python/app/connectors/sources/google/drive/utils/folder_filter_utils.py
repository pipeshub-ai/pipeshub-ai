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
from app.connectors.core.base.data_store.data_store import DataStoreProvider
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
    data_store_provider: DataStoreProvider,
    connector_id: str,
    file_id: str,
    tracked_folder_ids: set,
) -> Tuple[Optional[Record], bool]:
    """Load the persisted record for `file_id` and say whether its parent is in scope."""
    async with data_store_provider.transaction() as tx_store:
        existing_record = await tx_store.get_record_by_external_id(
            connector_id=connector_id,
            external_id=file_id,
        )

    if existing_record is None:
        return None, False

    return (
        existing_record,
        existing_record.parent_external_record_id in tracked_folder_ids,
    )


async def has_entered_scope(
    data_store_provider: DataStoreProvider,
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
        data_store_provider, connector_id, file_id, tracked_folder_ids
    )

    if existing_record is None:
        return True

    return not parent_in_scope


async def has_exited_scope(
    data_store_provider: DataStoreProvider,
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
        data_store_provider, connector_id, file_id, tracked_folder_ids
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
    """
    try:
        data_source = await get_data_source()
        response = await data_source.files_get(
            fileId=folder_id,
            fields=DRIVE_FOLDER_EXPANSION_GET_FIELDS,
            supportsAllDrives=True,
        )
    except HttpError as e:
        logger.debug(
            f"Folder {folder_id} is not visible to this user (HTTP {e.resp.status})"
        )
        return None
    except Exception as e:
        logger.warning(f"Failed to probe folder {folder_id}: {e}")
        return None

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
) -> AsyncGenerator[List[dict], None]:
    """
    Recursively fetch all descendants (files and folders) of a folder that
    just entered the tracked scope, yielding them in batches.

    Unlike `build_tracked_folder_ids`, this fetches every child item, not
    just folders, but only recurses into children that are themselves
    folders. Children already present in `changes_ids` (the current
    changes_list page) are skipped so they aren't processed twice: they
    are already flowing through the regular changes loop.
    """
    # Resolve driveId once so shared-drive subtrees use corpora=drive.
    root_drive_id: Optional[str] = None
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
