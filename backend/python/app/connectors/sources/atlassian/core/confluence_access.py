"""Access rules shared by the Confluence connectors, and the listing reads they depend on."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import parse_qs, urlparse

from app.config.constants.arangodb import MimeTypes
from app.models.entities import Record, RecordType
from app.models.permission import EntityType, Permission, PermissionType

if TYPE_CHECKING:
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
    Raises ValueError when a Cloud next link has no usable offset, so the caller fails the
    read instead of guessing one.
    """
    links = response_data.get("_links")
    if isinstance(links, dict) and links:
        next_url = links.get("next")
        if not next_url or not page_len:
            return None
        # Cloud's link points at the next page; Data Center's short page still links to start + limit (CONFSERVER-95272).
        if not use_link_offset:
            return start + page_len
        values = parse_qs(urlparse(next_url).query).get("start") or []
        if not values or not values[0].isdigit() or int(values[0]) <= start:
            raise ValueError(f"next page link has no usable start offset: {next_url}")
        return int(values[0])
    size = response_data.get("size", page_len)
    return start + page_len if size >= batch_size and page_len else None


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
            child.inherit_permissions = inherits_space
            await processor.on_updated_record_permissions(child, permissions)
            updated += 1
            if child.record_type in _COMMENT_TYPES:
                parents.append(child.external_record_id)
    return updated
