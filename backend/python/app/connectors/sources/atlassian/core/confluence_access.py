"""Access rules shared by the Confluence connectors, and the listing reads they depend on."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import parse_qs, urlparse

from app.config.constants.arangodb import MimeTypes
from app.config.constants.http_status_code import HttpStatusCode
from app.models.entities import Record, RecordType
from app.models.permission import EntityType, Permission, PermissionType

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )

_COMMENT_TYPES = (RecordType.COMMENT, RecordType.INLINE_COMMENT)
_PAGE_DEPENDENT_TYPES = (RecordType.FILE, *_COMMENT_TYPES)


def _is_folder(record: Record) -> bool:
    # A stored record read back as a base Record has no is_file, but keeps the folder mime type.
    return record.record_type == RecordType.FILE and (
        getattr(record, "is_file", True) is False or record.mime_type == MimeTypes.FOLDER.value
    )


@dataclass(frozen=True)
class Page:
    """One page of a Confluence listing, read in full (``read_page``)."""

    results: list[dict[str, Any]]
    links: dict[str, Any]

    @property
    def next_url(self) -> Optional[str]:
        return self.links.get("next")

    @property
    def base(self) -> Optional[str]:
        return self.links.get("base")


def user_key(item: dict[str, Any]) -> Optional[str]:
    """The id of a user: ``userKey`` / ``key`` (Data Center), ``accountId`` (Cloud) or ``username``."""
    value = item.get("userKey") or item.get("key") or item.get("accountId") or item.get("username")
    return value if isinstance(value, str) and value else None


def group_key(item: dict[str, Any]) -> Optional[str]:
    """The id of a group: ``name`` (Data Center) or ``id``."""
    value = item.get("name") or item.get("id")
    return value if isinstance(value, str) and value else None


def content_id(item: dict[str, Any]) -> Optional[str]:
    """The id of a page, blog post, comment or attachment."""
    value = item.get("id")
    return str(value) if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value) else None


def principal_id(item: dict[str, Any]) -> Optional[str]:
    """Who a Cloud v2 space permission row names: ``principal.id``."""
    principal = item.get("principal")
    return content_id(principal) if isinstance(principal, dict) else None


def read_page(body: object, item_id: Optional[Callable[[dict[str, Any]], Optional[str]]] = None) -> Optional[Page]:
    """A listing page, or None when it is not one read in full: unknown is not empty.

    The shape every Confluence REST listing documents (Data Center PageResponse, Cloud v1 and v2 lists):
    ``results`` a list of objects; ``size``, when given, the number of those results; ``_links``, when given,
    an object whose ``next``, when given, is a link. An empty page that still links onward is not the end of
    the listing, and with ``item_id``, an item without a usable id is not a readable item.
    """
    if not isinstance(body, dict):
        return None
    results = body.get("results")
    if not isinstance(results, list) or not all(isinstance(item, dict) for item in results):
        return None
    size = body.get("size")
    if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size != len(results)):
        return None
    links = body.get("_links", {})
    if not isinstance(links, dict):
        return None
    next_url = links.get("next")
    if next_url is not None and (not isinstance(next_url, str) or not next_url):
        return None
    next_url = next_url or continuation(body)
    if next_url and not results:
        return None
    if next_url and not links.get("next"):
        links = {**links, "next": next_url}
    if item_id is not None and not all(item_id(item) for item in results):
        return None
    return Page(results, links)


def continuation(body: dict[str, Any]) -> Optional[str]:
    """The next page a listing names: ``_links.next``, or the top-level ``next`` the 9.x child lists document."""
    links = body.get("_links") if isinstance(body.get("_links"), dict) else {}
    for value in (links.get("next"), body.get("next")):
        if isinstance(value, str) and value:
            return value
    return None


def v1_next_start(
    response_data: dict[str, Any],
    start: int,
    page_len: int,
    batch_size: int,
    *,
    use_link_offset: bool,
) -> Optional[int]:
    """Offset of the next page of a v1 offset-paged listing, or None at its end.

    Confluence can return fewer results than asked for before the end, so when the response
    has links, ``_links.next`` decides whether more pages follow. Without links, a full page
    means more. Pass ``use_link_offset=True`` on Cloud and False on Data Center.
    Raises ValueError when a Cloud next link has no usable offset, or when an empty page still
    links to a next one, so the caller fails the read instead of taking a cut listing as whole.
    """
    links = response_data.get("_links") if isinstance(response_data.get("_links"), dict) else {}
    next_url = continuation(response_data)
    if next_url:
        if not page_len:
            raise ValueError(f"an empty page links to a next one: {next_url}")
        # Cloud's link points at the next page. On Data Center ``start`` is "the index of the first item within the
        # result set" and the limit may be cut by fixed system limits, so the rows read decide, not the link.
        if not use_link_offset:
            return start + page_len
        values = parse_qs(urlparse(next_url).query).get("start") or []
        if not values or not values[0].isdigit() or int(values[0]) <= start:
            raise ValueError(f"next page link has no usable start offset: {next_url}")
        return int(values[0])
    total = response_data.get("totalCount")
    if isinstance(total, int) and not isinstance(total, bool) and page_len and start + page_len < total:
        return start + page_len  # Data Center 10+ counts the whole list
    if links:
        return None
    size = response_data.get("size")
    size = size if isinstance(size, int) and not isinstance(size, bool) else page_len
    return start + page_len if size >= batch_size and page_len else None


class ChildrenUnreadable(Exception):
    """A page's comment, reply or attachment list could not be read, or one of its items processed, in full.

    Raised instead of returning the rest: the item that holds the list fails and is synced again by id, so its
    checkpoint does not move past what was never written.
    """


def repeats(rows: list[dict[str, Any]], key: Callable[[dict[str, Any]], object], seen: set) -> bool:
    """True when a non-empty page lists only rows already read, as a server ignoring ``start`` answers; otherwise
    the page's rows are added to ``seen``. Such a list can't be read to its end, so its caller treats it as unread."""
    keys = {key(row) for row in rows}
    if rows and keys <= seen:
        return True
    seen |= keys
    return False


async def read_all_pages(
    fetch: Callable[[int, int], Awaitable[Any]],
    item_id: Optional[Callable[[dict[str, Any]], Optional[str]]] = None,
    batch_size: int = 100,
) -> Optional[tuple[list[dict[str, Any]], Optional[str]]]:
    """Every row of a Data Center offset-paged listing and its ``_links.base``; None when any page can't be read.

    ``fetch(start, limit)`` returns the HTTP response of one page. The next page starts where the rows read so far
    end (``v1_next_start``): ``start`` is "the index of the first item within the result set" and the limit "may be
    restricted by fixed system limits" (the Data Center REST specs), so neither the offset a next link names nor a
    short page decides it. A 404 on the first request means the content is gone, so it has no children; on a
    later page it is unread. Any other failed request, an exception, a page ``read_page`` refuses, or an empty page that links onward makes the whole listing unread.
    """
    rows: list[dict[str, Any]] = []
    base: Optional[str] = None
    start = 0
    seen: set = set()
    key = item_id or (lambda row: repr(sorted(row.items())))
    while True:
        try:
            response = await fetch(start, batch_size)
            if response is not None and response.status == HttpStatusCode.NOT_FOUND.value:
                # On the first request the content is gone (or out of sight), and its children with it. After rows
                # came back it existed a moment ago: a 404 then says nothing about the rows after them.
                return ([], None) if start == 0 else None
            body = response.json() if response is not None and response.status == HttpStatusCode.SUCCESS.value else None
        except Exception:
            return None
        page = read_page(body, item_id)
        if page is None or repeats(page.results, key, seen):
            return None
        rows.extend(page.results)
        base = base or page.base
        try:
            next_start = v1_next_start(body, start, len(page.results), batch_size, use_link_offset=False)
        except ValueError:
            return None
        if next_start is None:
            return rows, base
        start = next_start


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def listing_modified_after(filter_after: str | None, checkpoint: str | None, look_back_hours: int) -> str | None:
    """The "modified after" bound of a content listing: the later of the admin's filter and the checkpoint.

    Only the checkpoint is moved back ``look_back_hours``: CQL reads dates in the
    site's time zone, not UTC, so an incremental sync re-reads that far back to
    miss nothing. The admin's own date is used as set.
    """
    if not checkpoint:
        return filter_after
    since = _parse_iso(checkpoint) - timedelta(hours=look_back_hours)
    if filter_after and _parse_iso(filter_after) > since:
        return filter_after
    return since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def unresolved_principal_permission(principal_id: str, permission_type: PermissionType) -> Permission:
    """Grant for a restriction principal we have no user or group for yet.

    Dropping it could leave a restricted page with no grants, which reads as open to the
    whole space. Kept, it still marks the page restricted; no group matches it, so it
    grants nothing until the page is read again after that group is synced.
    """
    return Permission(external_id=principal_id, type=permission_type, entity_type=EntityType.GROUP)


async def inherit_page_access(processor: DataSourceEntitiesProcessor, record: Record) -> None:
    """Replace a stored file's or comment's own grants with its page's access, through inheritance."""
    record.inherit_permissions = True
    record.rewrite_permissions = True
    await processor.on_updated_record_permissions(record, [])


async def apply_page_access_to_dependents(
    processor: DataSourceEntitiesProcessor,
    connector_id: str,
    page_id: str,
    permissions: list[Permission],
    *,
    inherits_space: bool,
) -> int:
    """Give the stored files and comments of a page (and their replies and files) the page's access.

    Child pages and folders are left alone: they have restrictions of their own.
    Returns how many records were updated.
    """
    updated = 0
    seen = {page_id}
    parents = [page_id]
    while parents:
        parent_id = parents.pop()
        children = await processor.get_records_by_parent(
            connector_id=connector_id, parent_external_record_id=parent_id
        )
        for child in children:
            if (
                child.record_type not in _PAGE_DEPENDENT_TYPES
                or _is_folder(child)
                or child.external_record_id in seen
            ):
                continue
            seen.add(child.external_record_id)
            await inherit_page_access(processor, child)
            updated += 1
            if child.record_type in _COMMENT_TYPES:
                parents.append(child.external_record_id)
    return updated
