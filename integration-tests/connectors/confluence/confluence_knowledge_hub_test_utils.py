"""
Knowledge Hub API helpers for Confluence connector integration tests.

Asserts user-visible browse ACL via the same endpoints the frontend uses.
Requires Node.js gateway (PIPESHUB_BASE_URL) so OAuth client_credentials
resolves to the test user.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

from app.models.entities import Record, RecordType

if TYPE_CHECKING:
    from helper.graph_provider import GraphProviderProtocol
    from pipeshub_client import PipeshubClient

from connectors.confluence.confluence_v1_test_utils import (
    ContentItem,
    SpaceContentSnapshot,
)

logger = logging.getLogger("confluence-kh-test-utils")

KH_CHILDREN_LIMIT = 200
FOLDER_MIME_TYPE = "text/directory"

KhParentType = Literal["app", "recordGroup", "folder", "record"]


def find_kh_item(
    items: list[dict[str, Any]],
    *,
    node_id: str | None = None,
    name: str | None = None,
    node_type: str | None = None,
    record_type: str | None = None,
) -> dict[str, Any] | None:
    """Return the first Knowledge Hub item matching the given filters."""
    for item in items:
        if node_id is not None and str(item.get("id")) != str(node_id):
            continue
        if name is not None and item.get("name") != name:
            continue
        if node_type is not None and item.get("nodeType") != node_type:
            continue
        if record_type is not None and item.get("recordType") != record_type:
            continue
        return item
    return None


def _kh_permission_role(item: dict[str, Any]) -> str | None:
    permission = item.get("permission")
    if isinstance(permission, dict):
        role = permission.get("role")
        if role:
            return str(role)
    user_role = item.get("userRole")
    if user_role:
        return str(user_role)
    return None


def assert_kh_item_has_permission(item: dict[str, Any], *, context: str) -> str:
    role = _kh_permission_role(item)
    assert role, (
        f"{context}: Knowledge Hub item {item.get('id')!r} ({item.get('name')!r}) "
        f"has no permission.role (item keys: {sorted(item.keys())})"
    )
    return role


def _snapshot_by_external_id(snapshot: SpaceContentSnapshot) -> dict[str, ContentItem]:
    return {item.id: item for item in snapshot.all_content}


def _is_folder_record(record: Record) -> bool:
    return (record.mime_type or "").lower() == FOLDER_MIME_TYPE


def _kh_node_type_for_content_type(content_type: str) -> str:
    return "folder" if content_type == "folder" else "record"


def _kh_record_type_for_content_type(content_type: str) -> str | None:
    if content_type == "page":
        return RecordType.CONFLUENCE_PAGE.value
    if content_type == "blogpost":
        return RecordType.CONFLUENCE_BLOGPOST.value
    return None


def _kh_parent_type_for_record(record: Record, snapshot_by_id: dict[str, ContentItem]) -> KhParentType:
    """Return KH parent_type for drilling into this graph record's children."""
    content_item = snapshot_by_id.get(record.external_record_id)
    if content_item is not None:
        return "folder" if content_item.content_type == "folder" else "record"
    if _is_folder_record(record):
        return "folder"
    return "record"


class _KhChildrenCache:
    """Cache Knowledge Hub children lists keyed by (parent_type, parent_id)."""

    def __init__(self, client: "PipeshubClient") -> None:
        self._client = client
        self._cache: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def fetch(self, parent_type: KhParentType, parent_id: str) -> list[dict[str, Any]]:
        key = (parent_type, parent_id)
        if key not in self._cache:
            response = self._client.get_knowledge_hub_children(
                parent_type,
                parent_id,
                only_containers=False,
                limit=KH_CHILDREN_LIMIT,
                sort_by="name",
                sort_order="asc",
            )
            self._cache[key] = response.get("items") or []
        return self._cache[key]


async def _kh_parent_ref_for_record(
    graph_provider: "GraphProviderProtocol",
    connector_id: str,
    record: Record,
    space_rg_id: str,
    snapshot_by_id: dict[str, ContentItem],
    *,
    context: str,
) -> tuple[KhParentType, str]:
    """Resolve the KH parent node where *record* should appear as a direct child."""
    parent_external_id = record.parent_external_record_id
    if not parent_external_id:
        return "recordGroup", space_rg_id

    parent_record = await graph_provider.get_record_by_external_id(
        connector_id, parent_external_id
    )
    assert parent_record is not None, (
        f"{context}: parent external id={parent_external_id!r} not found in graph "
        f"for record {record.external_record_id!r}"
    )
    parent_type = _kh_parent_type_for_record(parent_record, snapshot_by_id)
    return parent_type, parent_record.id


def assert_kh_record_visible_in_parent(
    graph_record_id: str,
    kh_items: list[dict[str, Any]],
    *,
    parent_type: KhParentType,
    parent_id: str,
    name: str | None = None,
    node_type: str | None = None,
    record_type: str | None = None,
    context: str,
) -> dict[str, Any]:
    """Assert a record/folder is listed under its immediate KH parent."""
    item = find_kh_item(
        kh_items,
        node_id=graph_record_id,
        name=name,
        node_type=node_type,
        record_type=record_type,
    )
    assert item is not None, (
        f"{context}: graph record id={graph_record_id!r} (name={name!r}, "
        f"nodeType={node_type!r}, recordType={record_type!r}) not found in "
        f"Knowledge Hub {parent_type} {parent_id!r} children "
        f"({len(kh_items)} item(s))"
    )
    if node_type is not None:
        assert item.get("nodeType") == node_type, (
            f"{context}: expected nodeType={node_type!r}, got {item.get('nodeType')!r}"
        )
    if record_type is not None:
        assert item.get("recordType") == record_type, (
            f"{context}: expected recordType={record_type!r}, got {item.get('recordType')!r}"
        )
    if name is not None:
        assert item.get("name") == name, (
            f"{context}: expected name={name!r}, got {item.get('name')!r}"
        )
    assert_kh_item_has_permission(item, context=context)
    return item


async def assert_kh_content_item_visible(
    client: "PipeshubClient",
    graph_provider: "GraphProviderProtocol",
    connector_id: str,
    space_rg_id: str,
    content_item: ContentItem,
    *,
    snapshot_by_id: dict[str, ContentItem],
    kh_cache: _KhChildrenCache,
    context: str,
) -> None:
    """Assert one synced page/blogpost/folder is visible at the correct KH tree level."""
    graph_record = await graph_provider.get_record_by_external_id(
        connector_id, content_item.id
    )
    assert graph_record is not None, (
        f"{context}: {content_item.content_type} {content_item.id} "
        f"not found in graph before KH check"
    )

    parent_type, parent_id = await _kh_parent_ref_for_record(
        graph_provider,
        connector_id,
        graph_record,
        space_rg_id,
        snapshot_by_id,
        context=context,
    )
    kh_items = kh_cache.fetch(parent_type, parent_id)
    node_type = _kh_node_type_for_content_type(content_item.content_type)
    record_type = _kh_record_type_for_content_type(content_item.content_type)

    assert_kh_record_visible_in_parent(
        graph_record.id,
        kh_items,
        parent_type=parent_type,
        parent_id=parent_id,
        name=content_item.title,
        node_type=node_type,
        record_type=record_type,
        context=(
            f"{context} {content_item.content_type} {content_item.id} "
            f"(parent={parent_type}/{parent_id})"
        ),
    )


async def assert_kh_snapshot_content(
    client: "PipeshubClient",
    graph_provider: "GraphProviderProtocol",
    connector_id: str,
    space_rg_id: str,
    snapshot: SpaceContentSnapshot,
    *,
    context: str,
    content_types: frozenset[str] | None = None,
) -> None:
    """Assert all snapshot pages, blogposts, and/or folders are visible in Knowledge Hub."""
    snapshot_by_id = _snapshot_by_external_id(snapshot)
    kh_cache = _KhChildrenCache(client)

    items_to_check: list[ContentItem] = []
    for content_type, bucket in (
        ("page", snapshot.pages),
        ("blogpost", snapshot.blogposts),
        ("folder", snapshot.folders),
    ):
        if content_types is not None and content_type not in content_types:
            continue
        items_to_check.extend(bucket)

    assert items_to_check, f"{context}: no content items to validate in snapshot"

    for content_item in items_to_check:
        await assert_kh_content_item_visible(
            client,
            graph_provider,
            connector_id,
            space_rg_id,
            content_item,
            snapshot_by_id=snapshot_by_id,
            kh_cache=kh_cache,
            context=context,
        )

    counts = {
        "page": len(snapshot.pages),
        "blogpost": len(snapshot.blogposts),
        "folder": len(snapshot.folders),
    }
    checked = {
        content_type: len([i for i in items_to_check if i.content_type == content_type])
        for content_type in ("page", "blogpost", "folder")
    }
    logger.info(
        "%s: %d content item(s) visible in Knowledge Hub "
        "(pages=%d/%d, blogposts=%d/%d, folders=%d/%d)",
        context,
        len(items_to_check),
        checked["page"],
        counts["page"],
        checked["blogpost"],
        counts["blogpost"],
        checked["folder"],
        counts["folder"],
    )


def assert_kh_space_visible_for_user(
    client: "PipeshubClient",
    connector_id: str,
    space_rg_id: str,
    space_name: str,
    *,
    space_key: str | None = None,
    context: str,
) -> dict[str, Any]:
    """Assert the Confluence space RecordGroup is visible under the app with ACL."""
    response = client.get_knowledge_hub_children(
        "app",
        connector_id,
        only_containers=True,
        limit=KH_CHILDREN_LIMIT,
        sort_by="name",
        sort_order="asc",
    )
    items = response.get("items") or []
    item = find_kh_item(items, node_id=space_rg_id)
    if item is None and space_name:
        item = find_kh_item(
            items,
            name=space_name,
            node_type="recordGroup",
        )
    if item is None and space_key:
        item = find_kh_item(
            items,
            name=space_key,
            node_type="recordGroup",
        )

    assert item is not None, (
        f"{context}: Space RecordGroup id={space_rg_id!r} (name={space_name!r}) "
        f"not found in Knowledge Hub app children for connector {connector_id}. "
        f"Got {len(items)} item(s): "
        f"{[(i.get('id'), i.get('name'), i.get('nodeType')) for i in items[:10]]}"
    )
    assert item.get("nodeType") == "recordGroup", (
        f"{context}: expected nodeType=recordGroup, got {item.get('nodeType')!r}"
    )
    assert item.get("recordGroupType") == "CONFLUENCE_SPACES", (
        f"{context}: expected recordGroupType=CONFLUENCE_SPACES, "
        f"got {item.get('recordGroupType')!r}"
    )
    role = assert_kh_item_has_permission(item, context=context)
    logger.info(
        "%s: space RecordGroup %s visible with role=%s",
        context,
        space_rg_id,
        role,
    )
    return item


async def assert_kh_folder_from_snapshot(
    client: "PipeshubClient",
    graph_provider: "GraphProviderProtocol",
    connector_id: str,
    space_rg_id: str,
    folder_external_id: str,
    folder_title: str,
    *,
    snapshot: SpaceContentSnapshot,
    context: str,
) -> None:
    """Assert a synced folder is visible at the correct level in Knowledge Hub."""
    folder_item = next(
        (f for f in snapshot.folders if f.id == folder_external_id),
        ContentItem(
            id=folder_external_id,
            title=folder_title,
            version_number=0,
            space_id=snapshot.space_id,
            space_key=snapshot.space_key,
            content_type="folder",
        ),
    )
    await assert_kh_content_item_visible(
        client,
        graph_provider,
        connector_id,
        space_rg_id,
        folder_item,
        snapshot_by_id=_snapshot_by_external_id(snapshot),
        kh_cache=_KhChildrenCache(client),
        context=f"{context} folder {folder_external_id}",
    )
