"""Google Drive API v3 partial `fields` masks shared by files.list and files.get.

Drive returns a sparse default projection when `fields` is omitted on files.get;
these strings match the file metadata requested during sync so reindex stays
consistent with list.
"""

# Personal (OAuth) Drive connector — same inner projection as files.list in
# google/drive/individual/connector.py
DRIVE_PERSONAL_SYNC_FILE_RESOURCE_FIELDS = (
    "id, name, mimeType, size, createdTime, modifiedTime, webViewLink, fileExtension, "
    "headRevisionId, version, shared, md5Checksum, sha1Checksum, sha256Checksum, parents, "
    "driveId"
)

DRIVE_PERSONAL_SYNC_FILES_LIST_FIELDS = (
    f"nextPageToken, files({DRIVE_PERSONAL_SYNC_FILE_RESOURCE_FIELDS})"
)

# Workspace / delegated Drive connector lists include `owners`.
DRIVE_WORKSPACE_SYNC_FILE_RESOURCE_FIELDS = (
    "id, name, mimeType, size, createdTime, modifiedTime, webViewLink, fileExtension, "
    "headRevisionId, version, shared, owners, md5Checksum, sha1Checksum, sha256Checksum, parents, "
    "driveId, sharedWithMeTime"
)

DRIVE_WORKSPACE_SYNC_FILES_LIST_FIELDS = (
    f"nextPageToken, files({DRIVE_WORKSPACE_SYNC_FILE_RESOURCE_FIELDS})"
)

DRIVE_WORKSPACE_SYNC_CHANGES_LIST_FIELDS = (
    "nextPageToken, newStartPageToken, "
    f"changes(changeType, fileId, removed, file({DRIVE_WORKSPACE_SYNC_FILE_RESOURCE_FIELDS}))"
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
