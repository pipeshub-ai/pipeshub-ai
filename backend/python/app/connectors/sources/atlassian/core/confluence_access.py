"""Access rules shared by the Confluence connectors, and the listing reads they depend on."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import parse_qs, urlparse

from app.config.constants.arangodb import MimeTypes
from app.models.entities import Record, RecordType
from app.models.permission import EntityType, Permission, PermissionType

if TYPE_CHECKING:
    from collections.abc import Callable

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
    if next_url and not results:
        return None
    if item_id is not None and not all(item_id(item) for item in results):
        return None
    return Page(results, links)


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
    links = response_data.get("_links")
    if isinstance(links, dict) and links:
        next_url = links.get("next")
        if not next_url:
            return None
        if not page_len:
            raise ValueError(f"an empty page links to a next one: {next_url}")
        # Cloud's link points at the next page; Data Center's short page still links to start + limit (CONFSERVER-95272).
        if not use_link_offset:
            return start + page_len
        values = parse_qs(urlparse(next_url).query).get("start") or []
        if not values or not values[0].isdigit() or int(values[0]) <= start:
            raise ValueError(f"next page link has no usable start offset: {next_url}")
        return int(values[0])
    size = response_data.get("size", page_len)
    return start + page_len if size >= batch_size and page_len else None


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
            child.inherit_permissions = True
            child.rewrite_permissions = True
            await processor.on_updated_record_permissions(child, [])
            updated += 1
            if child.record_type in _COMMENT_TYPES:
                parents.append(child.external_record_id)
    return updated
