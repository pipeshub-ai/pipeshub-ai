"""Unit tests for UpdateOrderedListing: issues and merge requests read by update time."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.connectors.sources.gitlab.listing import UpdateOrderedListing
from app.sources.client.gitlab.gitlab import GitLabResponse


def _item(item_id: int, updated: str) -> SimpleNamespace:
    return SimpleNamespace(id=item_id, updated_at=updated)


def _iso(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


class _Page(list):
    """What python-gitlab hands back for ``iterator=True``: the page, and its paging headers."""

    def __init__(self, items: list, per_page: int, next_page: int | None) -> None:
        super().__init__(items)
        self.per_page = per_page
        self.next_page = next_page


def _server(items: list[SimpleNamespace], cap: int, calls: list[dict]) -> AsyncMock:
    """Answers like GitLab: ``updated_after``/``updated_before`` inclusive, ascending, offset pages."""

    def parse(v: datetime | None) -> float | None:
        return v.timestamp() if v else None

    async def ds_call(method, **kw) -> object:
        calls.append(kw)
        after, before = parse(kw.get("updated_after")), parse(kw.get("updated_before"))

        def t(i) -> float:
            return datetime.fromisoformat(i.updated_at.replace("Z", "+00:00")).timestamp()

        rows = sorted((i for i in items if (after is None or t(i) >= after - 1e-9)
                       and (before is None or t(i) <= before + 1e-9)), key=lambda i: (t(i), i.id))
        size = min(kw["per_page"], cap)
        page = kw["page"]
        chunk = rows[(page - 1) * size: page * size]
        more = page * size < len(rows)
        return GitLabResponse(success=True, data=_Page(chunk, size, page + 1 if more else None))

    return AsyncMock(side_effect=ds_call)


def _listing(ds_call: AsyncMock, page_size: int = 3) -> UpdateOrderedListing:
    runtime = MagicMock()
    runtime.ds_call = ds_call
    return UpdateOrderedListing(runtime, page_size=page_size)


async def _run(listing: UpdateOrderedListing) -> GitLabResponse:
    return await listing.list(MagicMock(), project_id=1, updated_after=None, updated_before=None,
                              created_after=None, created_before=None)


async def test_reads_every_item_in_update_order() -> None:
    items = [_item(n, _iso(1_000_000 + n * 10)) for n in range(1, 11)]
    calls: list[dict] = []
    res = await _run(_listing(_server(items, 100, calls)))
    assert res.success
    assert [i.id for i in res.data] == list(range(1, 11))
    assert all(c["page"] == 1 for c in calls), "a moving list must not be read by offset"


async def test_a_page_capped_below_the_size_asked_is_not_the_end() -> None:
    items = [_item(n, _iso(1_000_000 + n * 10)) for n in range(1, 11)]
    res = await _run(_listing(_server(items, 2, []), page_size=100))
    assert [i.id for i in res.data] == list(range(1, 11))


async def test_an_item_edited_between_pages_does_not_hide_another() -> None:
    items = [_item(n, _iso(1_000_000 + n * 10)) for n in range(1, 11)]
    calls: list[dict] = []
    inner = _server(items, 100, calls)

    async def ds_call(method, **kw) -> object:
        if len(calls) == 1:  # between the first and the second page
            items[0].updated_at = _iso(2_000_000)
        return await inner(method, **kw)

    res = await _run(_listing(AsyncMock(side_effect=ds_call)))
    assert {i.id for i in res.data} == set(range(1, 11))


async def test_a_whole_page_in_one_millisecond() -> None:
    items = [_item(n, _iso(1_000_000)) for n in range(1, 8)] + [_item(99, _iso(1_000_500))]
    res = await _run(_listing(_server(items, 100, []), page_size=3))
    assert {i.id for i in res.data} == {*range(1, 8), 99}


async def test_a_failed_page_says_so_and_keeps_what_was_read() -> None:
    items = [_item(n, _iso(1_000_000 + n * 10)) for n in range(1, 11)]
    inner = _server(items, 100, [])
    seen = {"n": 0}

    async def ds_call(method, **kw) -> object:
        seen["n"] += 1
        if seen["n"] == 2:
            return GitLabResponse(success=False, error="502", status_code=502)
        return await inner(method, **kw)

    res = await _run(_listing(AsyncMock(side_effect=ds_call)))
    assert res.success is False
    assert res.status_code == 502
    assert [i.id for i in res.data] == [1, 2, 3]
