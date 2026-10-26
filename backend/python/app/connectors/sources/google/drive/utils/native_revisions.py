"""Whether a Google Docs, Sheets or Slides file's content changed, when Drive only says the file did.

One job: answer that question for a native file whose `version` moved. Drive moves `version` on every change
to a file, a rename or a sharing change included, and native files have no headRevisionId or checksum, so
`version` alone re-indexed a document every time it was renamed or shared. Their content revisions are in
revisions.list, and the last revision's modifiedTime is never later than the file's own modifiedTime. So the
content changed since a record was stored exactly when the last revision is newer than the record's stored
modification time. Records store that time cut to whole seconds (`parse_timestamp`), so the revision's is cut
the same way before comparing; an edit in the same second as the stored modification waits for the next one.

Reading revisions needs edit access. When it is refused, the answer is "changed", which is what the connector
assumed before.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from googleapiclient.errors import HttpError

from app.connectors.sources.google.drive.utils.drive_filters import parse_drive_datetime

if TYPE_CHECKING:
    from logging import Logger

ListRevisions = Callable[..., Awaitable[dict[str, Any]]]
_FIELDS = "nextPageToken, revisions(id, modifiedTime)"
_MAX_PAGES = 50


async def native_content_changed(
    file_id: str, stored_modified_ms: int | None, list_revisions: ListRevisions, logger: "Logger"
) -> bool:
    if not stored_modified_ms:
        return True
    last: dict[str, Any] | None = None
    page_token: str | None = None
    try:
        for _ in range(_MAX_PAGES):
            page = await list_revisions(fileId=file_id, pageSize=1000, pageToken=page_token, fields=_FIELDS) or {}
            revisions = page.get("revisions") or []
            if revisions:
                last = revisions[-1]
            page_token = page.get("nextPageToken")
            if not page_token:
                break
        else:
            return True
    except HttpError as e:
        if e.resp.status in (403, 404):
            logger.debug("Revisions of %s could not be read (%s); treating it as changed", file_id, e.resp.status)
            return True
        raise
    revised_ms = parse_drive_datetime((last or {}).get("modifiedTime"))
    if revised_ms is None:
        return True
    return revised_ms // 1000 > stored_modified_ms // 1000
