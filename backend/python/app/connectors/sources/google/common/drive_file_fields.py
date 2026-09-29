"""Google Drive API v3 partial `fields` masks shared by files.list and files.get.

Drive returns a sparse default projection when `fields` is omitted on files.get;
these strings match the file metadata requested during sync so reindex stays
consistent with list.
"""

# shortcutDetails resolves a shortcut to its target. trashed distinguishes a move
# to trash from an edit. ownedByMe and capabilities distinguish the user's own
# files from files shared with them, and say whether they can edit.
_DRIVE_FILE_ACCESS_FIELDS = (
    "shortcutDetails/targetId, shortcutDetails/targetMimeType, trashed, ownedByMe, "
    "capabilities/canEdit, capabilities/canDownload"
)

# Personal (OAuth) Drive connector — same inner projection as files.list in
# google/drive/individual/connector.py
DRIVE_PERSONAL_SYNC_FILE_RESOURCE_FIELDS = (
    "id, name, mimeType, size, createdTime, modifiedTime, webViewLink, fileExtension, "
    "headRevisionId, version, shared, md5Checksum, sha1Checksum, sha256Checksum, parents, "
    f"driveId, {_DRIVE_FILE_ACCESS_FIELDS}"
)

DRIVE_PERSONAL_SYNC_FILES_LIST_FIELDS = (
    f"nextPageToken, incompleteSearch, files({DRIVE_PERSONAL_SYNC_FILE_RESOURCE_FIELDS})"
)

# changes.list for the personal connector. driveId lets member-drive changes be
# skipped here (those drives have their own feed). changeType=drive reports
# membership changes; the drive object names the drive that changed.
DRIVE_PERSONAL_SYNC_CHANGES_LIST_FIELDS = (
    "nextPageToken, newStartPageToken, incompleteSearch, "
    "changes(changeType, fileId, driveId, removed, drive(id,name), "
    f"file({DRIVE_PERSONAL_SYNC_FILE_RESOURCE_FIELDS}))"
)

# Workspace / delegated Drive connector lists include `owners`.
DRIVE_WORKSPACE_SYNC_FILE_RESOURCE_FIELDS = (
    "id, name, mimeType, size, createdTime, modifiedTime, webViewLink, fileExtension, "
    "headRevisionId, version, shared, owners, md5Checksum, sha1Checksum, sha256Checksum, parents, "
    f"driveId, sharedWithMeTime, {_DRIVE_FILE_ACCESS_FIELDS}"
)

DRIVE_WORKSPACE_SYNC_FILES_LIST_FIELDS = (
    f"nextPageToken, incompleteSearch, files({DRIVE_WORKSPACE_SYNC_FILE_RESOURCE_FIELDS})"
)

DRIVE_WORKSPACE_SYNC_CHANGES_LIST_FIELDS = (
    "nextPageToken, newStartPageToken, incompleteSearch, "
    "changes(changeType, fileId, driveId, removed, drive(id,name), "
    f"file({DRIVE_WORKSPACE_SYNC_FILE_RESOURCE_FIELDS}))"
)

# drives.list for shared drives the user is a member of. capabilities say whether
# that user can edit or manage members, without domain-admin access.
DRIVE_DRIVES_LIST_FIELDS = (
    "nextPageToken, incompleteSearch, "
    "drives(id,name,createdTime,capabilities/canEdit,capabilities/canManageMembers)"
)

# files.get for workspace reindex: same projection as list.
DRIVE_WORKSPACE_FILE_GET_FIELDS = DRIVE_WORKSPACE_SYNC_FILE_RESOURCE_FIELDS

# Folder-filter subtree expansion: folder identity plus whether this user may
# enumerate children. driveId on get lets shared-drive parents use corpora=drive
# instead of corpora=allDrives (which can incompleteSearch My Drive folders).
DRIVE_FOLDER_EXPANSION_LIST_FIELDS = (
    "nextPageToken, files(id, capabilities/canListChildren)"
)

DRIVE_FOLDER_EXPANSION_GET_FIELDS = "id, capabilities/canListChildren, driveId"
