"""Google Drive API v3 partial `fields` masks shared by files.list and files.get.

Drive returns a sparse default projection when `fields` is omitted on files.get;
these strings match the file metadata requested during sync so reindex stays
consistent with list.
"""

# Personal (OAuth) Drive connector — same inner projection as files.list in
# google/drive/individual/connector.py
DRIVE_PERSONAL_SYNC_FILE_RESOURCE_FIELDS = (
    "id, name, mimeType, size, createdTime, modifiedTime, webViewLink, fileExtension, "
    "headRevisionId, version, shared, md5Checksum, sha1Checksum, sha256Checksum, parents"
)

DRIVE_PERSONAL_SYNC_FILES_LIST_FIELDS = (
    f"nextPageToken, files({DRIVE_PERSONAL_SYNC_FILE_RESOURCE_FIELDS})"
)

# Workspace / delegated Drive connector lists include `owners`.
DRIVE_WORKSPACE_SYNC_FILE_RESOURCE_FIELDS = (
    "id, name, mimeType, size, createdTime, modifiedTime, webViewLink, fileExtension, "
    "headRevisionId, version, shared, owners, md5Checksum, sha1Checksum, sha256Checksum, parents"
)

DRIVE_WORKSPACE_SYNC_FILES_LIST_FIELDS = (
    f"nextPageToken, files({DRIVE_WORKSPACE_SYNC_FILE_RESOURCE_FIELDS})"
)

# files.get for workspace reindex: same as list item fields plus driveId for shared-drive detection.
DRIVE_WORKSPACE_FILE_GET_FIELDS = f"{DRIVE_WORKSPACE_SYNC_FILE_RESOURCE_FIELDS}, driveId"
