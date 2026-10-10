"""Knowledge Hub Unified Browse Service"""

import asyncio
import inspect
import logging
import re
import traceback
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    FOLDER_MIME_TYPES,
    CollectionNames,
    ProgressStatus,
)
from app.config.constants.service import config_node_constants
from app.connectors.sources.localKB.api.knowledge_hub_models import (
    AppliedFilters,
    AvailableFilters,
    BreadcrumbItem,
    CountItem,
    CountsInfo,
    CurrentNode,
    FilterOption,
    FiltersInfo,
    ItemPermission,
    KnowledgeHubNodesResponse,
    NodeItem,
    NodeType,
    OriginType,
    PaginationInfo,
    ParentRef,
    PermissionsInfo,
    SortField,
    SortOrder,
)
from app.connectors.sources.localKB.handlers.kh_search import SearchPage, search_page
from app.models.entities import RecordType, substitute_user_email
from app.modules.demo_data.access import excluded_demo_connector_ids
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.kh_cursor import (
    Boundary,
    CursorError,
    KnowledgeHubCursor,
    decode,
    derive_cursor_secret,
    encode,
)
from app.utils.user_messages import action_failed, not_found


class BrowseRequestError(Exception):
    """A browse request this service can explain to the person who made it.

    Only messages written here for a reader travel in one of these. Everything
    else that goes wrong — including a ``ValueError`` the graph client raises for
    its own reasons, such as a transaction it can no longer find — is internal,
    and the caller answers it with a generic message instead.
    """

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# A page number re-traverses: every page walked is one listing query, however
# few rows it holds, so the bound counts pages. Cursors have no such cost.
_MAX_PAGE_WALK = 50

# Filter options list the user's sources, and a tenant with more Apps and
# collections than this has a filter dropdown nobody can use anyway.
_MAX_FILTER_SOURCES = 500

_START_TYPES = {
    "app": "app", "kb": "app", "recordGroup": "recordGroup",
    "folder": "record", "record": "record",
}


def _start_type(parent_type: str | None) -> str:
    """The kind of node a scoped listing starts from, for the type in the URL."""
    return _START_TYPES.get(parent_type or "", "record")


def _listing_mode(parent_id: str | None, flatten: bool) -> str:
    if parent_id is None:
        return "search" if flatten else "root"
    return "flatten" if flatten else "browse"


def _without_apps(access: dict[str, Any], app_ids: frozenset[str]) -> dict[str, Any]:
    """The access context with these Apps out of the gate and their grants dropped."""
    if not app_ids:
        return access
    out = {**access, "gated_app_ids": [a for a in access.get("gated_app_ids") or [] if a not in app_ids]}
    if access.get("by_connector") is not None:
        out["by_connector"] = {k: v for k, v in access["by_connector"].items() if k not in app_ids}
    return out


class KnowledgeHubService:
    """Service for unified Knowledge Hub browse API"""

    # The API's sort names to the provider's, one map for every listing mode.
    _SORT_FIELDS = {
        "name": "name",
        "createdAt": "createdAt",
        "updatedAt": "updatedAt",
        "size": "sizeInBytes",
        "type": "nodeType",
    }

    def __init__(
        self,
        logger: logging.Logger,
        graph_provider: IGraphDBProvider,
        config_service: ConfigurationService | None = None,
        excluded_app_ids: frozenset[str] | None = None,
    ) -> None:
        self.logger = logger
        self.graph_provider = graph_provider
        self.config_service = config_service
        self._cursor_secret: bytes | None = None
        # The Acme Corp demo, for someone who switched it off. Callers that
        # already know it pass `excluded_app_ids`; otherwise it is read per
        # request from `config_service`.
        self._excluded_app_ids = excluded_app_ids

    async def _excluded_apps(self, user_id: str, org_id: str) -> frozenset[str]:
        if self._excluded_app_ids is not None:
            return self._excluded_app_ids
        if self.config_service is None:
            return frozenset()
        try:
            return await excluded_demo_connector_ids(
                self.graph_provider, self.config_service, org_id, user_id
            )
        except Exception as exc:
            self.logger.warning("demo data setting unreadable for user=%s: %s", user_id, exc)
            return frozenset()

    async def _get_cursor_secret(self) -> bytes | None:
        """The cursor-signing key, derived once, or None when nothing can sign.

        An unsigned cursor is an editable one, and what a cursor carries decides
        what the next page looks at, so a caller with no config service (the
        agent tools, which page by number) gets pages **without** cursors rather
        than unsigned ones, and any cursor it is handed is refused.
        """
        if self._cursor_secret is None:
            if self.config_service is None:
                return None
            secret_keys = await self.config_service.get_config(
                config_node_constants.SECRET_KEYS.value
            )
            self._cursor_secret = derive_cursor_secret(
                (secret_keys or {}).get("scopedJwtSecret")
            )
        return self._cursor_secret

    def _sort_field(self, sort_by: str) -> str:
        return self._SORT_FIELDS.get(sort_by, "name")

    def _decode_cursor(
        self,
        token: str | None,
        secret: bytes | None,
        user_id: str,
        org_id: str,
        *,
        mode: str,
        parent_id: str | None = None,
        parent_type: str | None = None,
    ) -> KnowledgeHubCursor | None:
        """A cursor that does not verify, or was issued by another listing, is a
        400, never a silent first page.

        A caller's cursor is refused up front when nothing can verify it, so a
        token reaching here unsigned is one this service made to walk to a page
        number and never handed out.
        """
        if not token:
            return None
        try:
            cursor = decode(token, secret, expected_user_id=user_id, expected_org_id=org_id)
        except CursorError as exc:
            raise BrowseRequestError(f"Invalid cursor: {exc}", 400) from exc
        # A cursor that names no mode fails here too: it cannot say where it is from.
        issued_for = (cursor.mode, cursor.parent_id, _start_type(cursor.parent_type))
        if issued_for != (mode, parent_id, _start_type(parent_type)):
            raise BrowseRequestError("Invalid cursor: issued for a different location", 400)
        return cursor

    async def _walk_pages(
        self,
        fetch_page: Callable[[str | None], Awaitable[SearchPage]],
        cursor_token: str | None,
        page: int,
    ) -> SearchPage:
        """One page, by cursor if given, otherwise by walking forward to `page`."""
        if cursor_token:
            return await fetch_page(cursor_token)
        if page > _MAX_PAGE_WALK:
            raise BrowseRequestError(
                f"Invalid page: page must not exceed {_MAX_PAGE_WALK}. "
                f"Use the cursor from a previous response.",
                400,
            )
        result = await fetch_page(None)
        for _ in range(page - 1):
            if not result.next_cursor:
                return replace(
                    result, rows=[], start_index=0, end_index=0,
                    next_cursor=None, prev_cursor=None,
                )
            result = await fetch_page(result.next_cursor)
        return result

    def _page_from_partition(
        self,
        part: dict[str, Any],
        cursor: KnowledgeHubCursor | None,
        secret: bytes,
        *,
        filters: dict[str, Any],
        sort_field: str,
        sort_dir: str,
        parent_id: str | None,
        parent_type: str | None,
        mode: str,
        user_id: str,
        org_id: str,
        want_counts: bool,
    ) -> SearchPage:
        """A single-partition listing (root or scoped) as a page, with its cursors.

        Browse and a scoped flatten are one query, so there is nothing to merge
        — but the page shape, the boundary and the carried total are the same
        as a global search's, which is what lets one response builder serve both.
        """
        rows = part["rows"]
        direction = cursor.direction if cursor else "next"

        if cursor is not None and cursor.total is not None:
            total, counts = cursor.total, cursor.counts_by_type
        else:
            total = part["total"] if part["total"] is not None else len(rows)
            if want_counts and part.get("counts") is not None:
                counts = part["counts"]
            elif want_counts:
                counts = dict(Counter(entry["nodeType"] for entry in part["ids"]))
            else:
                counts = None

        seen_before = 0
        if cursor is not None:
            seen_before = (
                cursor.items_seen if direction == "next"
                else max(0, cursor.items_seen - len(rows))
            )
        # Going back, `hasMore` means more rows lie *before* this page; the page
        # ahead is the one the caller just came from and always exists.
        has_next = part["hasMore"] if direction == "next" else True
        has_prev = (cursor is not None) if direction == "next" else part["hasMore"]

        def issue(row: dict[str, Any], next_direction: str, items_seen: int) -> str:
            return encode(
                KnowledgeHubCursor(
                    boundary=Boundary.of(row),
                    direction=next_direction,
                    items_seen=items_seen,
                    total=total,
                    counts_by_type=counts,
                    filters=filters,
                    sort_by=sort_field,
                    sort_order=sort_dir,
                    parent_id=parent_id,
                    parent_type=parent_type,
                    via_parent_id=cursor.via_parent_id if cursor else None,
                    mode=mode,
                    user_id=user_id,
                    org_id=org_id,
                ),
                secret,
            )

        return SearchPage(
            rows=rows,
            total=total,
            counts_by_type=counts,
            start_index=seen_before + 1 if rows else 0,
            end_index=seen_before + len(rows),
            next_cursor=issue(rows[-1], "next", seen_before + len(rows))
            if rows and has_next else None,
            prev_cursor=issue(rows[0], "prev", seen_before)
            if rows and has_prev else None,
        )

    async def _v2_nodes(
        self,
        *,
        user_id: str,
        user_key: str,
        org_id: str,
        parent_id: str | None,
        parent_type: str | None,
        limit: int,
        page: int,
        cursor: str | None,
        sort_by: str,
        sort_order: str,
        filters: dict[str, Any],
        flatten: bool,
        want_counts: bool,
        access: dict[str, list[str]],
        excluded: frozenset[str] = frozenset(),
    ) -> tuple[SearchPage, dict[str, Any] | None]:
        """One page of a listing or search, plus the `scope` a scoped request carries.

        Three modes, one shape: a global search is partitioned and merged, while
        a root listing and a scoped browse or flatten are each a single query
        that also returns `currentNode`, `parentNode` and the breadcrumb trail.
        """
        secret = await self._get_cursor_secret()
        # Nothing can verify it, so it cannot be trusted to say where to resume.
        if cursor and secret is None:
            raise BrowseRequestError("Invalid cursor: cursor paging is not configured", 400)
        sort_field = self._sort_field(sort_by)
        sort_dir = "ASC" if sort_order.lower() == "asc" else "DESC"
        mode = _listing_mode(parent_id, flatten)

        if parent_id is None and flatten:
            # The search partitions by connector, so it needs the grants too; a
            # provider that reads them per connector does so inside each page.
            if getattr(self.graph_provider, "kh_grants_per_connector", False) is not True:
                access = _without_apps(
                    await self.graph_provider.get_knowledge_hub_access_v3(user_key=user_key, org_id=org_id),
                    excluded,
                )

            async def fetch_global(token: str | None) -> SearchPage:
                self._decode_cursor(token, secret, user_id, org_id, mode=mode)
                return await search_page(
                    self.graph_provider,
                    user_key=user_key, user_id=user_id, org_id=org_id,
                    limit=limit, sort_field=sort_field, sort_dir=sort_dir,
                    filters=filters, cursor_token=token, secret=secret,
                    access=access,
                )
            try:
                global_page = await self._walk_pages(fetch_global, cursor, page)
            except CursorError as exc:
                raise BrowseRequestError(f"Invalid cursor: {exc}", 400) from exc
            return global_page, None

        scope: dict[str, Any] | None = None

        async def fetch_single(token: str | None) -> SearchPage:
            nonlocal scope
            page_cursor = self._decode_cursor(
                token, secret, user_id, org_id,
                mode=mode, parent_id=parent_id, parent_type=parent_type,
            )
            # The cursor decides sort and filters, so a page cannot resume a
            # keyset from an order that no longer applies.
            active = dict(page_cursor.filters or {}) if page_cursor else dict(filters)
            field = (page_cursor.sort_by if page_cursor else None) or sort_field
            order = (page_cursor.sort_order if page_cursor else None) or sort_dir
            reuse_total = page_cursor is not None and page_cursor.total is not None

            common = {
                "user_key": user_key, "org_id": org_id, "limit": limit,
                "sort_field": field, "sort_dir": order,
                "after": page_cursor.boundary.as_after() if page_cursor else None,
                "direction": page_cursor.direction if page_cursor else "next",
                "include_ids": want_counts and not reuse_total,
            }
            if parent_id is None:
                envelope = await self.graph_provider.get_knowledge_hub_root_nodes_v2(
                    user_app_ids=access["gated_app_ids"],
                    # Apps are in no record group, and this filter only narrows
                    # collection-origin groups.
                    **{k: v for k, v in active.items() if k != "record_group_ids"},
                    **common,
                )
            else:
                start_type = _start_type(parent_type)
                page = await self.graph_provider.get_knowledge_hub_connector_page_v3(
                    app_id=parent_id if start_type == "app" else "",
                    org_id=org_id,
                    grantee_ids=access["grantee_ids"],
                    gated_app_ids=access["gated_app_ids"],
                    # Browsing stays inside one connector: the page reads that
                    # connector's grants, not every grant the user holds.
                    granted_ids=None,
                    user_key=user_key,
                    limit=limit,
                    flatten=flatten,
                    sort_field=field,
                    sort_dir=order,
                    after=page_cursor.boundary.as_after() if page_cursor else None,
                    direction=page_cursor.direction if page_cursor else "next",
                    filters=active,
                    include_total=not reuse_total,
                    start_id=parent_id,
                    start_type=start_type,
                    include_scope=True,
                    via_parent_id=page_cursor.via_parent_id if page_cursor else None,
                )
                envelope = {
                    "partitions": [{
                        "rows": page["rows"],
                        "hasMore": page["hasMore"],
                        "total": page["total"],
                        "ids": [],
                        "counts": page.get("counts"),
                    }],
                    "scope": page.get("scope"),
                }
            scope = envelope.get("scope")
            return self._page_from_partition(
                envelope["partitions"][0], page_cursor, secret,
                filters=active, sort_field=field, sort_dir=order,
                parent_id=parent_id, parent_type=parent_type, mode=mode,
                user_id=user_id, org_id=org_id, want_counts=want_counts,
            )

        return await self._walk_pages(fetch_single, cursor, page), scope

    def _only_signed_cursors(self, page: SearchPage, secret: bytes | None) -> SearchPage:
        """Hand out cursors only when they are signed.

        Walking to a `page` number builds cursors internally to step forward, so
        they exist either way, but an unsigned one must never leave the process,
        where it becomes an editable instruction about what to read next.
        """
        if secret is not None:
            return page
        return replace(page, next_cursor=None, prev_cursor=None)

    def _to_current_node(self, crumb: dict[str, Any] | None) -> CurrentNode | None:
        if not crumb or not crumb.get('id'):
            return None
        return CurrentNode(
            id=crumb['id'],
            name=crumb.get('name') or '',
            nodeType=crumb.get('nodeType') or '',
            subType=crumb.get('subType'),
        )

    async def warm_browse_plans(self) -> int:
        """Browse each kind of place once, as the UI does, for a sample user, and
        drop the answers: the database plans a statement the first time it sees
        it (seconds for the listing and the access check) and again when its
        statistics move, and this takes that cost off a person's click. Returns
        how many requests were made."""
        sample = await self.graph_provider.get_knowledge_hub_warm_sample()
        if not sample:
            return 0
        full = ["counts", "permissions", "breadcrumbs", "availableFilters"]
        main = {"limit": 50, "sort_by": "updatedAt", "sort_order": "desc", "include": full}
        requests: list[dict[str, Any]] = [
            main,
            {"limit": 20, "sort_by": "updatedAt", "sort_order": "desc", "include": ["counts"]},
        ]
        for parent_type, key in (("app", "appId"), ("recordGroup", "groupId"), ("record", "recordId")):
            parent = {"parent_id": sample[key], "parent_type": parent_type}
            requests.append({**main, **parent})
            if parent_type != "record":
                requests.append({
                    **parent, "limit": 20, "sort_by": "name", "sort_order": "asc", "only_containers": True,
                    "include": full if parent_type == "recordGroup" else None,
                })
        for request in requests:
            try:
                await self.get_nodes(user_id=sample["userId"], org_id=sample["orgId"], **request)
            except Exception as exc:
                self.logger.warning("knowledge hub plan warm-up request failed: %s", exc)
        return len(requests)

    async def _shared(self, key: tuple, compute: Callable[[], Awaitable[Any]]) -> Any:
        """``compute()``, shared with the other request of the same click while it
        runs (``IGraphDBProvider._kh_v3_shared``): the main pane and the sidebar ask
        for the same user and the same gate at the same moment. The answer is read,
        never changed."""
        share = getattr(type(self.graph_provider), "_kh_v3_shared", None)
        if not inspect.iscoroutinefunction(share):
            return await compute()
        return await self.graph_provider._kh_v3_shared(key, compute)

    async def _belongs_to(self, node_id: str, node_type: str | None, app_ids: frozenset[str]) -> bool:
        """Whether a browsed node is one of `app_ids` or sits inside one."""
        if not app_ids:
            return False
        if node_type in ("app", "kb"):
            return node_id in app_ids
        collection = CollectionNames.RECORD_GROUPS.value if node_type == "recordGroup" else CollectionNames.RECORDS.value
        doc = await self.graph_provider.get_document(node_id, collection)
        return bool(doc) and doc.get("connectorId") in app_ids

    async def _resolve_user(self, user_id: str, org_id: str) -> Any | None:
        """Resolve graph user node from external userId. EE overrides for org-scoped lookup."""
        return await self.graph_provider.get_user_by_user_id(user_id=user_id)

    async def _get_user_app_ids(
        self, user_key: str, org_id: str
    ) -> list[str]:
        """Return app IDs accessible to this user. EE overrides for org-scoped lookup."""
        owned_app_ids = await self.graph_provider.get_user_app_ids(user_key)
        shared_app_ids = await self.graph_provider.get_user_permission_app_ids(user_key, org_id)
        return list(dict.fromkeys([*owned_app_ids, *shared_app_ids]))

    def _has_flattening_filters(self, q: str | None, node_types: list[str] | None,
                                 record_types: list[str] | None, origins: list[str] | None,
                                 connector_ids: list[str] | None,
                                 indexing_status: list[str] | None,
                                 created_at: dict | None, updated_at: dict | None,
                                 size: dict | None) -> bool:
        """Check if any filters that should trigger flattened/recursive search are provided.

        These filters should return flattened results (all nested children):
        - q, nodeTypes, recordTypes, origins, connectorIds,
          createdAt, updatedAt, size, indexingStatus
        Note: sortBy and sortOrder are NOT included as they don't trigger flattening.

        This is only the FALLBACK computation used when the caller doesn't pass
        an explicit `flattened` flag — see get_nodes() for the precedence rule.
        """
        return any([q, node_types, record_types, origins, connector_ids,
                    indexing_status, created_at, updated_at, size])

    async def get_nodes(
        self,
        user_id: str,
        org_id: str,
        parent_id: str | None = None,
        parent_type: str | None = None,
        only_containers: bool = False,
        page: int = 1,
        limit: int = 50,
        sort_by: str = "updatedAt",
        sort_order: str = "desc",
        q: str | None = None,
        node_types: list[str] | None = None,
        record_types: list[str] | None = None,
        origins: list[str] | None = None,
        connector_ids: list[str] | None = None,
        indexing_status: list[str] | None = None,
        created_at: dict[str, int | None] | None = None,
        updated_at: dict[str, int | None] | None = None,
        size: dict[str, int | None] | None = None,
        flattened: bool | None = None,
        include: list[str] | None = None,
        record_group_ids: list[str] | None = None,
        depth: int | None = None,
        include_typed_records: bool = False,
        cursor: str | None = None,
        is_org_admin: bool | None = None,
    ) -> KnowledgeHubNodesResponse:
        """
        Get nodes for the Knowledge Hub unified browse API

        `is_org_admin` is the caller's org role from the auth token; the root
        permissions block needs it because graph User nodes carry no role.

        `flattened` precedence: if the caller passes it explicitly (True or
        False), it always decides search-vs-browse mode. Only when it's
        omitted (None) do we fall back to computing it from which filters
        are present (see _has_flattening_filters).
        """
        filters_task: asyncio.Task | None = None
        permissions_task: asyncio.Task | None = None
        try:
            page = max(1, page)
            limit = min(max(1, limit), 200)  # Max 200

            # Get user key
            user = await self._shared(("user", user_id, org_id), lambda: self._resolve_user(user_id, org_id))
            if not user:
                return KnowledgeHubNodesResponse(
                    success=False,
                    error="User not found",
                    errorCode=404,
                    id=parent_id,
                    items=[],
                    pagination=PaginationInfo(
                        page=page, limit=limit, totalItems=0, totalPages=0,
                        hasNext=False, hasPrev=False
                    ),
                    filters=FiltersInfo(applied=AppliedFilters()),
                )
            user_key = user.get('_key')
            # Switched-off demo data leaves the gate, so a deep link into it
            # answers like a missing node, in any mode.
            excluded = await self._excluded_apps(user_id, org_id)

            # `flattened`, when explicitly passed by the caller, always wins.
            # Otherwise fall back to computing it from which filters are present
            # (any of q/nodeTypes/recordTypes/origins/connectorIds/indexingStatus/
            # createdAt/updatedAt/size triggers the flattened/recursive search).
            if flattened is not None:
                use_search_mode = flattened
            else:
                use_search_mode = self._has_flattening_filters(
                    q, node_types, record_types, origins, connector_ids,
                    indexing_status, created_at, updated_at, size
                )

            # Browse applies filters too: one query serves browse, flatten and
            # search.
            filters = {
                "search_query": q,
                "node_types": node_types,
                "record_types": record_types,
                "indexing_status": indexing_status,
                "created_at": created_at,
                "updated_at": updated_at,
                "size": size,
                "origins": origins,
                "connector_ids": connector_ids,
                "record_group_ids": record_group_ids,
                "only_containers": only_containers,
            }
            # Who the user is, resolved once: the listing queries, the global
            # search and the filter options all gate on this same answer, and
            # asking three times would let them disagree.
            access = _without_apps(
                await self._shared(
                    ("gate", user_key, org_id),
                    lambda: self.graph_provider.get_knowledge_hub_access_context_v2(user_key=user_key, org_id=org_id),
                ),
                excluded,
            )
            # The filter options and the context permissions read neither the
            # page nor each other, so they run beside it rather than after it.
            if include and 'availableFilters' in include:
                filters_task = asyncio.create_task(self._get_available_filters(user_key, org_id, access))
            if include and 'permissions' in include:
                permissions_task = asyncio.create_task(self._get_permissions(
                    user_key, org_id, parent_id, parent_type, is_org_admin=is_org_admin,
                ))
            page_result, scope = await self._v2_nodes(
                user_id=user_id,
                user_key=user_key,
                org_id=org_id,
                access=access,
                excluded=excluded,
                parent_id=parent_id,
                parent_type=parent_type,
                limit=limit,
                page=page,
                cursor=cursor,
                sort_by=sort_by,
                sort_order=sort_order,
                filters=filters,
                flatten=use_search_mode,
                want_counts=bool(include and 'counts' in include),
            )

            # The start node's own admission decides this, and the 404 body is
            # constant: naming the node, or its type, would confirm it exists
            # to someone who may not see it.
            if scope is not None and not scope.get('admitted'):
                raise BrowseRequestError("Node not found", 404)

            # Browsing with the wrong type in the URL is a 400. A folder *is* a
            # record in the graph, so those two are one type here: comparing
            # the raw values would reject every folder browse.
            actual_type = ((scope or {}).get('currentNode') or {}).get('nodeType')
            if parent_type and actual_type:
                same = {'folder': 'record'}
                if same.get(actual_type, actual_type) != same.get(parent_type, parent_type):
                    raise BrowseRequestError(
                        f"Node type mismatch: node '{parent_id}' is not '{parent_type}', "
                        f"it is '{actual_type}'. Use /nodes/{actual_type}/{parent_id} instead.",
                        400,
                    )

            # Whether more rows exist is known either way; only the cursors
            # themselves are withheld from a caller that pages by number.
            has_next = page_result.next_cursor is not None
            has_prev = page_result.prev_cursor is not None
            page_result = self._only_signed_cursors(page_result, await self._get_cursor_secret())

            await self._stamp_collection_roles(page_result.rows, user_key)
            items = [self._doc_to_node_item(row) for row in page_result.rows]
            user_email = user.get('email')
            for item in items:
                if item.webUrl:
                    item.webUrl = substitute_user_email(item.webUrl, user_email, item.connector)
            total_count = page_result.total or 0

            available_filters = await filters_task if filters_task is not None else None

            total_pages = (total_count + limit - 1) // limit if total_count > 0 else 0
            current_node = self._to_current_node((scope or {}).get('currentNode'))
            parent_node = self._to_current_node((scope or {}).get('parentNode'))

            # Build applied filters
            applied_filters = AppliedFilters(
                q=q,
                nodeTypes=node_types,
                recordTypes=record_types,
                origins=origins,
                connectorIds=connector_ids,
                indexingStatus=indexing_status,
                createdAt=created_at,
                updatedAt=updated_at,
                size=size,
                sortBy=sort_by,
                sortOrder=sort_order,
            )

            # Build filters info (without available filters initially)
            filters_info = FiltersInfo(applied=applied_filters)

            # Build response
            response = KnowledgeHubNodesResponse(
                success=True,
                id=parent_id,
                currentNode=current_node,
                parentNode=parent_node,
                items=items,
                pagination=PaginationInfo(
                    limit=limit,
                    totalItems=total_count,
                    hasNext=has_next,
                    hasPrev=has_prev,
                    startIndex=page_result.start_index,
                    endIndex=page_result.end_index,
                    currentPageItems=len(items),
                    nextCursor=page_result.next_cursor,
                    prevCursor=page_result.prev_cursor,
                    # Legacy, and meaningless once the caller pages by cursor.
                    page=page if cursor is None else None,
                    totalPages=total_pages if cursor is None else None,
                ),
                filters=filters_info,
            )

            # Fetch typed records if requested (for LLM context enrichment)
            if include_typed_records and items:
                record_ids = [
                    item.id for item in items
                    if item.nodeType in (NodeType.RECORD, NodeType.FOLDER)
                ]
                if record_ids:
                    try:
                        response.typed_records = await self.graph_provider.get_typed_records_batch(
                            record_ids
                        )
                    except Exception as e:
                        self.logger.warning("Failed to fetch typed records: %s", e)

            # Add optional expansions
            if include:
                if 'availableFilters' in include:
                    # Add available filters only when requested
                    response.filters.available = available_filters

                if 'breadcrumbs' in include and scope:
                    # The placement trail: an ancestor the user cannot open is
                    # replaced by where the node actually appears, never named.
                    response.breadcrumbs = [
                        BreadcrumbItem(
                            id=crumb['id'],
                            name=crumb.get('name') or '',
                            nodeType=crumb.get('nodeType') or '',
                            subType=crumb.get('subType'),
                        )
                        for crumb in (scope.get('breadcrumbs') or [])
                        if crumb.get('id')
                    ]

                if 'counts' in include:
                    # Whole-result counts, taken on the first page and then
                    # carried in the cursor, so they agree with the total.
                    type_counts = page_result.counts_by_type or {}

                    # Map nodeType to display label
                    label_map = {
                        'app': 'apps',
                        'folder': 'folders',
                        'recordGroup': 'groups',
                        'record': 'records',
                    }

                    count_items = [
                        CountItem(
                            label=label_map.get(node_type, node_type),
                            count=count
                        )
                        for node_type, count in sorted(type_counts.items())
                    ]

                    response.counts = CountsInfo(
                        items=count_items,
                        total=total_count,  # Use actual total count, not paginated length
                    )

                if permissions_task is not None:
                    response.permissions = await permissions_task

            return response

        except BrowseRequestError as request_error:
            self.logger.warning("⚠️ Browse request refused: %s", request_error.message)
            return KnowledgeHubNodesResponse(
                success=False,
                error=request_error.message,  # user-written message
                errorCode=request_error.status_code,
                id=parent_id,
                items=[],
                pagination=PaginationInfo(
                    page=page, limit=limit, totalItems=0, totalPages=0,
                    hasNext=False, hasPrev=False
                ),
                filters=FiltersInfo(applied=AppliedFilters()),
            )
        except Exception as e:
            self.logger.error("❌ Failed to get nodes: %s", e, exc_info=True)
            return KnowledgeHubNodesResponse(
                success=False,
                error=action_failed("open this collection"),
                errorCode=500,
                id=parent_id,
                items=[],
                pagination=PaginationInfo(
                    page=page, limit=limit, totalItems=0, totalPages=0,
                    hasNext=False, hasPrev=False
                ),
                filters=FiltersInfo(applied=AppliedFilters()),
            )
        finally:
            for task in (filters_task, permissions_task):
                if task is None:
                    continue
                if not task.done():
                    task.cancel()
                elif not task.cancelled():
                    task.exception()  # retrieved, so a discarded failure is not logged as unhandled

    async def _get_available_filters(
        self,
        user_key: str,
        org_id: str,
        access: dict[str, list[str]] | None = None,
    ) -> AvailableFilters:
        """The static filter enums, plus the sources this user can actually open.

        The sources are the same `gated_app_ids` the listing queries admit, read
        back through the root listing so the label and type come from the row
        the user would see, already ordered case-insensitively by name. That
        keeps them org-scoped and includes collections and Apps reached only
        through a grantee's permission.

        `get_knowledge_hub_filter_options` stays for its other callers (the
        agent catalog and the chat bridge), which ask a different question.
        """
        try:
            if access is None:
                access = await self.graph_provider.get_knowledge_hub_access_context_v2(
                    user_key=user_key, org_id=org_id
                )

            app_options: list[FilterOption] = []
            if access.get("gated_app_ids"):
                listing = await self.graph_provider.get_knowledge_hub_root_nodes_v2(
                    user_key=user_key,
                    org_id=org_id,
                    user_app_ids=access["gated_app_ids"],
                    limit=_MAX_FILTER_SOURCES,
                    names_only=True,
                )
                app_options = [
                    FilterOption(
                        id=row["id"],
                        label=row.get("name") or row["id"],
                        connectorType=row.get("connector") or row.get("origin"),
                    )
                    for row in listing["partitions"][0]["rows"]
                ]

            # Node type labels mapping
            node_type_labels = {
                NodeType.FOLDER: "Folder",
                NodeType.RECORD: "File",
                NodeType.RECORD_GROUP: "Drive/Root",
                NodeType.APP: "Connector",
            }

            return AvailableFilters(
                nodeTypes=[
                    FilterOption(
                        id=nt.value,
                        label=node_type_labels.get(nt, nt.value)
                    )
                    for nt in NodeType
                ],
                recordTypes=[
                    FilterOption(
                        id=rt.value,
                        label=self._format_enum_label(rt.value)
                    )
                    for rt in RecordType
                ],
                origins=[
                    FilterOption(
                        id=ot.value,
                        label="Collection" if ot == OriginType.COLLECTION else "External Connector"
                    )
                    for ot in OriginType
                ],
                connectors=app_options,
                indexingStatus=[
                    FilterOption(
                        id=status.value,
                        label=self._format_enum_label(status.value, {"AUTO_INDEX_OFF": "Manual Indexing"})
                    )
                    for status in ProgressStatus
                ],
                sortBy=[
                    FilterOption(
                        id=sf.value,
                        label=self._format_enum_label(sf.value, {"createdAt": "Created Date", "updatedAt": "Modified Date"})
                    )
                    for sf in SortField
                ],
                sortOrder=[
                    FilterOption(
                        id=so.value,
                        label="Ascending" if so == SortOrder.ASC else "Descending"
                    )
                    for so in SortOrder
                ]
            )
        except Exception as e:
            self.logger.error(f"Failed to get available filters: {e}")
            return AvailableFilters()

    async def _get_permissions(
        self,
        user_key: str,
        org_id: str,
        parent_id: str | None,
        parent_type: str | None = None,
        *,
        is_org_admin: bool | None = None,
    ) -> PermissionsInfo | None:
        """Get user permissions for the current context. Returns None if user has no permission."""
        if not parent_id and is_org_admin is not None:
            # The providers' root branch reads role/orgRole off the graph User,
            # which is never written there, so it answers MEMBER for every admin.
            return PermissionsInfo(
                role="ADMIN" if is_org_admin else "MEMBER",
                canUpload=is_org_admin,
                canCreateFolders=is_org_admin,
                canEdit=is_org_admin,
                canDelete=is_org_admin,
                canManagePermissions=is_org_admin,
            )
        try:
            perm_data = await self.graph_provider.get_knowledge_hub_context_permissions(
                user_key=user_key,
                org_id=org_id,
                parent_id=parent_id,
                parent_type=parent_type,
            )

            # If role is None, user has no permission - return None
            role = perm_data.get('role')
            if role is None:
                return None

            return PermissionsInfo(
                role=role,
                canUpload=perm_data.get('canUpload', False),
                canCreateFolders=perm_data.get('canCreateFolders', False),
                canEdit=perm_data.get('canEdit', False),
                canDelete=perm_data.get('canDelete', False),
                canManagePermissions=perm_data.get('canManagePermissions', False),
                collectionRole=await self._collection_role(user_key, org_id, parent_id, parent_type),
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to get permissions: {str(e)}")
            self.logger.error(traceback.format_exc())
            # Return None on error (no permission granted)
            return None

    async def _collection_role(
        self, user_key: str, org_id: str, parent_id: str | None, parent_type: str | None,
    ) -> str | None:
        """The user's role on the collection a node is in, from the same check restore uses.

        The context role ranks record permissions inside a folder, which has no
        FILEORGANIZER, so it can't say who may open the collection's trash.
        """
        if not parent_id or parent_type not in ("app", "folder", "record"):
            return None
        try:
            kb_id = parent_id
            if parent_type != "app":
                doc = await self.graph_provider.get_document(parent_id, CollectionNames.RECORDS.value)
                if not isinstance(doc, dict) or doc.get("orgId") != org_id:
                    return None
                kb_id = doc.get("connectorId")
            if not kb_id:
                return None
            role = await self.graph_provider.get_user_kb_permission(kb_id, user_key)
            return role if isinstance(role, str) else None
        except Exception as e:
            self.logger.warning("Could not read the collection role for %s: %s", parent_id, e)
            return None

    def _doc_to_node_item(self, doc: dict[str, Any]) -> NodeItem:
        """Convert a database document to a NodeItem"""
        # Extract ID - prefer 'id' field, fallback to '_key' or parse from '_id'
        doc_id = doc.get('id')
        if not isinstance(doc_id, str) or not doc_id.strip():
            if doc.get('_key'):
                doc_id = doc['_key']
            elif doc.get('_id'):
                _id_value = doc['_id']
                if isinstance(_id_value, str) and '/' in _id_value:
                    doc_id = _id_value.split('/', 1)[1]
                else:
                    doc_id = _id_value
            else:
                doc_id = ''

        node_type_str = doc.get('nodeType', 'record')
        try:
            node_type = NodeType(node_type_str)
        except ValueError:
            node_type = NodeType.RECORD

        # Get origin
        origin_str = doc.get('origin', 'COLLECTION')
        origin = OriginType.COLLECTION if origin_str == 'COLLECTION' else OriginType.CONNECTOR

        # Convert userRole to ItemPermission if present
        permission = None
        user_role = doc.get('userRole')
        if user_role:
            # Handle case where userRole might be a list (defensive safeguard)
            if isinstance(user_role, list):
                user_role = user_role[0] if user_role else None
            if user_role:
                permission = self._role_to_permission(user_role)

        # The parent's id, type and name travel with the row, so a search hit
        # renders "in <folder>" without a second lookup. Without the type a
        # client cannot build the parent's own URL.
        parent = None
        if doc.get('parentId') and doc.get('parentType'):
            parent = ParentRef(
                id=doc['parentId'],
                nodeType=doc['parentType'],
                name=doc.get('parentName'),
            )

        # Build NodeItem
        return NodeItem(
            id=doc_id,
            name=doc.get('name', ''),
            nodeType=node_type,
            parentId=doc.get('parentId'),
            parent=parent,
            origin=origin,
            connector=doc.get('connector'),
            connectorId=doc.get('connectorId'),
            recordType=doc.get('recordType'),
            recordGroupType=doc.get('recordGroupType'),
            indexingStatus=doc.get('indexingStatus'),
            reason=doc.get('reason'),
            createdAt=doc.get('createdAt', 0),
            updatedAt=doc.get('updatedAt', 0),
            sizeInBytes=doc.get('sizeInBytes'),
            mimeType=doc.get('mimeType'),
            extension=doc.get('extension'),
            webUrl=doc.get('webUrl'),
            hasChildren=doc.get('hasChildren', False),
            previewRenderable=doc.get('previewRenderable'),
            permission=permission,
            sharingStatus=doc.get('sharingStatus'),
            isInternal=bool(doc.get('isInternal', False)),
            isPlaceholder=bool(doc.get('isPlaceholder', False)),
        )


    async def _stamp_collection_roles(self, rows: list[dict], user_key: str) -> None:
        """The listing leaves userRole off everything below an App. Collection
        content inherits flat from its collection, so its role is the caller's
        role on that collection, and the collections page gates edit and delete
        on it. One lookup per collection on the page, all at once."""
        kb_ids = list({
            row.get('connectorId') for row in rows
            if row.get('origin') == 'COLLECTION' and row.get('nodeType') != 'app'
            and not row.get('userRole') and row.get('connectorId')
        })
        roles = dict(zip(kb_ids, await asyncio.gather(*(
            self.graph_provider.get_user_kb_permission(kb_id, user_key) for kb_id in kb_ids
        ))))
        for row in rows:
            if row.get('connectorId') in roles and row.get('nodeType') != 'app' and not row.get('userRole'):
                row['userRole'] = roles[row['connectorId']]

    def _role_to_permission(self, role: str) -> ItemPermission:
        """
        Convert a user role string to ItemPermission object with computed flags.

        Permission hierarchy:
        - OWNER: Full control (edit + delete all)
        - EDITOR: Can edit content
        - WRITER: Can edit and delete folders/records
        - COMMENTER, READER: Read-only (no edit, no delete)
        """
        role_upper = role.upper() if role else ''

        # Determine edit and delete permissions based on role
        can_edit = role_upper in ['OWNER', 'WRITER']
        can_delete = role_upper in ['OWNER', 'WRITER']

        return ItemPermission(
            role=role,
            canEdit=can_edit,
            canDelete=can_delete,
        )

    def _format_enum_label(self, value: str, special_cases: dict[str, str] | None = None) -> str:
        """
        Convert enum value to human-readable label.

        Handles both UPPER_SNAKE_CASE and camelCase:
        - "FILE_NAME" → "File Name"
        - "createdAt" → "Created At"
        - "autoIndexOff" → "Auto Index Off"

        Args:
            value: The enum value to format
            special_cases: Optional dict of special case mappings that differ from generic formatting

        Returns:
            Human-readable label
        """
        if special_cases and value in special_cases:
            return special_cases[value]

        # Handle camelCase by inserting space before uppercase letters
        # Insert space before uppercase letters that follow lowercase letters
        spaced = re.sub(r'([a-z])([A-Z])', r'\1 \2', value)
        # Replace underscores with spaces
        spaced = spaced.replace("_", " ")
        # Title case each word
        return spaced.title()
