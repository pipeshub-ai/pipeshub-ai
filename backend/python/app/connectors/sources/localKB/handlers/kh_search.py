"""One page of a global knowledge hub search, one query per connector.

The user's grants are bucketed by connector, each connector returns one page of
what the user may see, and those pages are merged into one ordered page.

Access is resolved per page, never carried in the cursor: the grantees and the
connector list are recomputed on every request, so a grant removed between two
pages takes effect on the next one, and a cursor's connector positions are
resolved against the list *this* request discovered. The cursor carries where
the page stopped, not what the user may see.

A failed connector fails the request: a page assembled from the connectors that
answered looks like a complete one, and the rows it omits are indistinguishable
from rows the user has no access to.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from typing import Any

from app.connectors.sources.localKB.handlers.kh_merge import PartitionFeed, merge_pages
from app.utils.kh_cursor import (
    Boundary,
    KnowledgeHubCursor,
    LivePartitions,
    decode,
    encode,
)

# None means no cap. A caller can still pass a positive limit.
DEFAULT_MAX_CONCURRENCY = None


@dataclass(frozen=True)
class SearchPage:
    rows: list[dict[str, Any]]
    total: int | None
    counts_by_type: dict[str, int] | None
    start_index: int
    end_index: int
    next_cursor: str | None
    prev_cursor: str | None


def _connector_key(app_id: str) -> str:
    return f"CONNECTOR:{app_id}"


async def search_page(
    provider: Any,
    *,
    user_key: str,
    user_id: str,
    org_id: str,
    limit: int,
    sort_field: str = "name",
    sort_dir: str = "ASC",
    filters: dict[str, Any] | None = None,
    cursor_token: str | None = None,
    secret: str | bytes | None = None,
    access: dict[str, Any] | None = None,
    max_concurrency: int | None = DEFAULT_MAX_CONCURRENCY,
) -> SearchPage:
    """Assemble one page. A bad cursor raises `CursorError`; the caller answers 400.

    ``sort_field`` and ``sort_dir`` are the provider's own names, the same ones
    the query's comparator uses, so nothing re-maps them between the cursor and
    the query.
    """
    cursor = None
    if cursor_token:
        cursor = decode(
            cursor_token, secret, expected_user_id=user_id, expected_org_id=org_id
        )
        # The cursor decides sort and filters; only `include` comes from the
        # request. A page that honoured a conflicting sort would resume a
        # keyset from an order that no longer applies and silently skip rows.
        filters = cursor.filters or {}
        sort_field = cursor.sort_by or sort_field
        sort_dir = cursor.sort_order or sort_dir

    filters = dict(filters or {})
    direction = cursor.direction if cursor else "next"
    descending = sort_dir.upper() == "DESC"

    # Grants grouped by connector. A provider that reads them per connector
    # does so inside each page, all at once, rather than in one statement that
    # has to finish before any page can start. Otherwise an access dict that
    # lacks the grouping is read again here.
    grants_read: dict[str, list[str]] | None = None
    if access is not None and access.get("by_connector") is None and access.get("gated_app_ids") is not None \
            and getattr(provider, "kh_grants_per_connector", False) is True:
        grants_read = {}
        connectors = sorted(access["gated_app_ids"])
    else:
        if access is None or access.get("by_connector") is None:
            access = await provider.get_knowledge_hub_access_v3(
                user_key=user_key, org_id=org_id,
            )
        connectors = sorted(access["by_connector"])
    wanted = filters.get("connector_ids")
    if wanted:
        allowed = set(wanted)
        connectors = [app_id for app_id in connectors if app_id in allowed]
    keys = [_connector_key(app_id) for app_id in connectors]
    targets = _targets(connectors, keys, cursor)

    # The total is counted once, on the first page, and then carried in the
    # cursor. A node belongs to one connector, so the counts sum to the total.
    reuse_total = cursor is not None and cursor.total is not None
    results = await _run(
        provider, targets,
        org_id=org_id, access=access, limit=limit,
        sort_field=sort_field, sort_dir=sort_dir, filters=filters,
        after=cursor.boundary.as_after() if cursor else None,
        direction=direction, include_total=not reuse_total,
        max_concurrency=max_concurrency,
        user_key=user_key, grants_read=grants_read,
    )
    if grants_read is not None:
        access = {**access, "by_connector": grants_read}

    merged = merge_pages(
        [
            PartitionFeed(
                partition_id=_connector_key(app_id),
                partition_kind="CONNECTOR",
                # A previous page arrives in page order from each connector; the
                # reverse merge walks outwards from the boundary, so each feed
                # has to start at the row nearest it.
                rows=iter(list(reversed(payload["rows"])) if direction == "prev"
                          else payload["rows"]),
            )
            for app_id, payload in zip(targets, results)
        ],
        limit=limit,
        descending=descending,
        reverse=direction == "prev",
    )

    # One batch check names the parents of the rows this page returns.
    await provider.name_knowledge_hub_parents(merged.rows, org_id, access)

    more_beyond = any(
        payload["hasMore"] or _connector_key(app_id) not in merged.exhausted
        for app_id, payload in zip(targets, results)
    )
    live = {
        _connector_key(app_id)
        for app_id, payload in zip(targets, results)
        if payload["hasMore"] or _connector_key(app_id) not in merged.exhausted
    }

    if reuse_total:
        total, counts = cursor.total, cursor.counts_by_type
    else:
        total = sum(payload.get("total") or 0 for payload in results)
        counts_counter: Counter[str] = Counter()
        for payload in results:
            counts_counter.update(payload.get("counts") or {})
        counts = dict(counts_counter)

    seen_before = 0
    if cursor is not None:
        seen_before = (
            cursor.items_seen if direction == "next"
            else max(0, cursor.items_seen - len(merged.rows))
        )

    def issue(
        boundary: Boundary,
        next_direction: str,
        items_seen: int,
        live_set: set[str] | None,
    ) -> str:
        return encode(
            KnowledgeHubCursor(
                boundary=boundary,
                live=LivePartitions.of(keys, live_set) if live_set is not None else None,
                direction=next_direction,
                items_seen=items_seen,
                total=total,
                counts_by_type=counts,
                filters=filters,
                sort_by=sort_field,
                sort_order=sort_dir,
                mode="search",
                user_id=user_id,
                org_id=org_id,
            ),
            secret,
        )

    has_next = more_beyond if direction == "next" else True
    has_prev = (cursor is not None) if direction == "next" else more_beyond

    next_cursor = prev_cursor = None
    if merged.rows:
        if has_next:
            # A page reached by going back knows nothing about which connectors
            # still hold rows *ahead* of it, so it carries no live set and the
            # next page queries them all — correct, only less selective.
            next_cursor = issue(
                merged.last, "next", seen_before + len(merged.rows),
                live if direction == "next" else None,
            )
        if has_prev:
            # Going back never carries a live set: a connector that ran out
            # going forward can still hold rows behind the boundary.
            prev_cursor = issue(merged.first, "prev", seen_before, None)

    return SearchPage(
        rows=merged.rows,
        total=total,
        counts_by_type=counts,
        start_index=seen_before + 1 if merged.rows else 0,
        end_index=seen_before + len(merged.rows),
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
    )


def _targets(
    connectors: list[str],
    keys: list[str],
    cursor: KnowledgeHubCursor | None,
) -> list[str]:
    """The connectors this page queries.

    Everything, unless the cursor names a live subset that still lines up with
    the list this request just built. Nothing here narrows by filter: the
    filters already run inside each query, and a connector skipped by a wrong
    guess drops rows silently rather than failing.
    """
    if cursor is None or cursor.live is None:
        return list(connectors)
    selected = cursor.live.select(keys)
    if selected is None:
        return list(connectors)
    live = set(selected)
    return [
        app_id for app_id, key in zip(connectors, keys) if key in live
    ]


async def _run(
    provider: Any,
    targets: list[str],
    *,
    org_id: str,
    access: dict[str, Any],
    limit: int,
    sort_field: str,
    sort_dir: str,
    filters: dict[str, Any],
    after: dict[str, Any] | None,
    direction: str,
    include_total: bool,
    max_concurrency: int | None,
    user_key: str | None = None,
    grants_read: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """One page per connector. Any failure aborts the page. With ``grants_read``
    each page reads its connector's grants itself and leaves them there."""
    gate = _gate(max_concurrency)
    granted = access.get("by_connector") or {}
    own_grants = {} if grants_read is None else {"user_key": user_key, "grants_out": grants_read}

    async def run(app_id: str) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            return await provider.get_knowledge_hub_connector_page_v3(
                app_id=app_id,
                org_id=org_id,
                grantee_ids=access["grantee_ids"],
                gated_app_ids=access["gated_app_ids"],
                granted_ids=None if grants_read is not None else granted[app_id],
                limit=limit,
                flatten=True,
                sort_field=sort_field,
                sort_dir=sort_dir,
                after=after,
                direction=direction,
                filters=filters,
                include_total=include_total,
                # Named once for the merged page, not once per connector.
                name_parents=False,
                **own_grants,
            )

        if gate is None:
            return await call()
        async with gate:
            return await call()

    # A TaskGroup cancels its siblings when one raises, so a failed connector
    # does not leave the rest querying for a page nobody will return.
    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(run(app_id)) for app_id in targets]
    return [task.result() for task in tasks]


def _gate(max_concurrency: int | None) -> asyncio.Semaphore | None:
    """A cap, or None when every call should run at once."""
    if max_concurrency is None or max_concurrency <= 0:
        return None
    return asyncio.Semaphore(max_concurrency)
