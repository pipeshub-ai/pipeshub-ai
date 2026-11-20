"""Global search assembles one page from many connectors, and resumes it exactly.

The fake provider below sorts, keysets and pages exactly as the real queries do
(it reuses their comparator through `kh_merge.key_for`), so these tests are
about the orchestration: which connectors run, how their pages merge, what the
cursors carry, and what happens when one connector fails or access changes
between pages.
"""

import asyncio
from collections import Counter
from typing import TYPE_CHECKING

import pytest

from app.connectors.sources.localKB.handlers.kh_merge import key_for
from app.connectors.sources.localKB.handlers.kh_search import search_page
from app.exceptions.graph_db_exceptions import (
    KnowledgeHubListingBusy,
    KnowledgeHubListingTooLarge,
)
from app.utils.kh_cursor import Boundary, CursorError, KnowledgeHubCursor

if TYPE_CHECKING:
    from app.connectors.sources.localKB.handlers.kh_search import SearchPage

USER_KEY, USER_ID, ORG = "u-key", "user-u", "org-1"
SECRET = "test-secret"


def row(row_id: str, name: str, *, parent: str = "par-1", internal: bool = False,
        node_type: str = "record") -> dict:
    return {
        "id": row_id, "name": name, "sortKey": name.lower(), "nullRank": 0,
        "nodeType": node_type, "parentId": parent, "parentIsInternal": internal,
    }


class FakeProvider:
    """One page per connector, the way the real provider returns it."""

    def __init__(self, rows_by_connector: dict[str, list[dict]], *, fails: set[str] = frozenset()) -> None:
        self.rows_by_connector = rows_by_connector
        self.fails = set(fails)
        self.calls: list[dict] = []
        self.named: list[list[str]] = []
        self.in_flight = 0
        self.peak_in_flight = 0

    async def get_knowledge_hub_access_v3(self, *, user_key, org_id, **_) -> dict:
        app_ids = sorted(self.rows_by_connector)
        return {
            "grantee_ids": [user_key, "group-g"],
            "gated_app_ids": app_ids,
            "by_connector": {app_id: [f"grant-{app_id}"] for app_id in app_ids},
        }

    async def name_knowledge_hub_parents(self, rows, org_id, access, **_) -> None:
        self.named.append([row["id"] for row in rows])

    async def get_knowledge_hub_connector_page_v3(self, *, app_id, **kwargs) -> dict:
        self.calls.append({"app_id": app_id, **kwargs})
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0)
            if app_id in self.fails:
                raise RuntimeError(f"connector {app_id} failed")
            descending = kwargs["sort_dir"].upper() == "DESC"
            rows = sorted(
                self.rows_by_connector.get(app_id, []),
                key=lambda candidate: key_for(candidate, descending=descending),
            )
            matching = list(rows)
            after, limit = kwargs.get("after"), kwargs["limit"]
            if after is not None:
                edge = key_for(after, descending=descending)
                # Strictly past the boundary in the page's direction: the
                # boundary row itself belongs to the page the caller already has.
                if kwargs["direction"] == "next":
                    rows = [
                        candidate for candidate in rows
                        if key_for(candidate, descending=descending) > edge
                    ]
                else:
                    rows = [
                        candidate for candidate in rows
                        if key_for(candidate, descending=descending) < edge
                    ]
            page = rows[:limit] if kwargs["direction"] == "next" else rows[-limit:]
            include_total = kwargs.get("include_total", True)
            counts: dict[str, int] = {}
            if include_total:
                for candidate in matching:
                    counts[candidate["nodeType"]] = counts.get(candidate["nodeType"], 0) + 1
            return {
                "rows": page,
                "hasMore": len(rows) > limit,
                "total": len(matching) if include_total else None,
                "counts": counts if include_total else None,
            }
        finally:
            self.in_flight -= 1


async def _search(provider, **kwargs) -> "SearchPage":
    return await search_page(
        provider, user_key=USER_KEY, user_id=USER_ID, org_id=ORG,
        secret=SECRET, **kwargs,
    )


def ids(page) -> list[str]:
    return [r["id"] for r in page.rows]


def test_partitions_merge_into_one_ordered_page() -> None:
    provider = FakeProvider({
        "rg-1": [row("a1", "apple"), row("a3", "cherry")],
        "kb-1": [row("a2", "banana")],
    })
    page = asyncio.run(_search(provider, limit=10))
    assert ids(page) == ["a1", "a2", "a3"]
    assert (page.start_index, page.end_index) == (1, 3)
    assert page.next_cursor is None and page.prev_cursor is None


def test_kb_and_connector_partitions_merge_across_a_paged_walk() -> None:
    """Four partitions of both kinds, merged into one name-ordered walk.

    The partitions together hold more than one page, so the first page carries
    a next cursor and the second does not.

    The names interleave the partitions deliberately -- no page is drawn from a
    single partition -- so a merge that simply concatenated partitions would
    fail rather than happening to agree.
    """
    provider = FakeProvider({
        "rg-1": [row("a1", "alpha"), row("a5", "echo")],
        "rg-2": [row("a3", "charlie")],
        "kb-1": [row("a2", "bravo"), row("a6", "foxtrot")],
        "kb-2": [row("a4", "delta")],
    })
    first = asyncio.run(_search(provider, limit=3))
    second = asyncio.run(_search(provider, limit=3, cursor_token=first.next_cursor))

    assert ids(first) == ["a1", "a2", "a3"]
    assert ids(second) == ["a4", "a5", "a6"]
    assert first.prev_cursor is None and first.next_cursor is not None
    assert second.next_cursor is None and second.prev_cursor is not None
    assert [(p.start_index, p.end_index) for p in (first, second)] == [(1, 3), (4, 6)]


def test_each_connector_is_one_flattened_page() -> None:
    """One call per connector, flatten on, and only that connector's grants."""
    provider = FakeProvider({
        "kb-1": [row("k1", "kb one")],
        "rg-1": [row("g1", "group one")],
    })
    asyncio.run(_search(provider, limit=10))
    by_app = {call["app_id"]: call for call in provider.calls}
    assert set(by_app) == {"kb-1", "rg-1"}
    assert by_app["kb-1"]["flatten"] is True
    assert by_app["rg-1"]["flatten"] is True
    assert by_app["kb-1"]["granted_ids"] == ["grant-kb-1"]
    assert by_app["rg-1"]["granted_ids"] == ["grant-rg-1"]


def test_a_depth_bound_reaches_every_connector_page() -> None:
    provider = FakeProvider({"kb-1": [row("k1", "kb one")], "rg-1": [row("g1", "group one")]})
    asyncio.run(_search(provider, limit=10, max_depth=2))
    assert {call["max_depth"] for call in provider.calls} == {2}
    provider = FakeProvider({"kb-1": [row("k1", "kb one")]})
    asyncio.run(_search(provider, limit=10))
    assert provider.calls[0]["max_depth"] == 50


def test_parents_are_named_once_for_the_merged_page() -> None:
    """Each connector's page comes back unnamed; the page this request returns
    is named in one batch check, not one per connector."""
    provider = FakeProvider({
        "rg-1": [row("a1", "alpha"), row("a3", "charlie")],
        "rg-2": [row("a2", "bravo"), row("a4", "delta")],
    })
    page = asyncio.run(_search(provider, limit=3))
    assert all(call["name_parents"] is False for call in provider.calls)
    assert provider.named == [ids(page)] == [["a1", "a2", "a3"]]


def test_a_record_in_two_partitions_appears_once_under_its_real_parent() -> None:
    """Shared with Me and the drive group both find the file."""
    provider = FakeProvider({
        "rg-drive": [row("g1", "file.txt", parent="rg-drive")],
        "rg-swm": [row("g1", "file.txt", parent="rg-swm", internal=True)],
    })
    page = asyncio.run(_search(provider, limit=10))
    assert ids(page) == ["g1"]
    assert page.rows[0]["parentId"] == "rg-drive"
    # Each connector counts the copy it returned. The merge still shows it once.
    assert page.total == 2
    assert page.counts_by_type == {"record": 2}


def test_counts_describe_the_whole_result_not_the_page() -> None:
    """The union of ids, counted by type, once."""
    provider = FakeProvider({
        "rg-1": [row("a1", "apple"), row("a3", "cherry", node_type="folder")],
        "kb-1": [row("a2", "banana")],
    })
    page = asyncio.run(_search(provider, limit=1))
    assert ids(page) == ["a1"]
    assert page.total == 3
    assert page.counts_by_type == {"record": 2, "folder": 1}


def test_a_later_page_reuses_the_carried_total_and_refetches_no_ids() -> None:
    """Recounting per page pays twice for a number the cursor already holds."""
    provider = FakeProvider({
        "rg-1": [row("a1", "apple"), row("a3", "cherry")],
        "kb-1": [row("a2", "banana")],
    })
    first = asyncio.run(_search(provider, limit=2))
    assert all(call["include_total"] for call in provider.calls)
    provider.calls.clear()

    second = asyncio.run(_search(provider, limit=2, cursor_token=first.next_cursor))
    assert ids(second) == ["a3"]
    assert (second.total, second.counts_by_type) == (3, {"record": 3})
    assert not any(call["include_total"] for call in provider.calls), "the total was recounted"


class BoundaryCountingProvider(FakeProvider):
    """The real queries count what they select: with a boundary, only the rows past it."""

    async def get_knowledge_hub_connector_page_v3(self, *, app_id, **kwargs) -> dict:
        page = await super().get_knowledge_hub_connector_page_v3(app_id=app_id, **kwargs)
        if kwargs.get("after") is not None and page["total"] is not None:
            descending = kwargs["sort_dir"].upper() == "DESC"
            edge = key_for(kwargs["after"], descending=descending)
            past = [r for r in self.rows_by_connector.get(app_id, []) if key_for(r, descending=descending) > edge]
            page = {**page, "total": len(past), "counts": dict(Counter(r["nodeType"] for r in past))}
        return page


def _clock(monkeypatch, start_ms: int = 1_000_000_000_000) -> list[int]:
    import app.utils.kh_cursor as kh_cursor
    from app.connectors.sources.localKB.handlers import kh_search

    clock = [start_ms]
    monkeypatch.setattr(kh_cursor.time, "time", lambda: clock[0] / 1000)
    monkeypatch.setattr(kh_search.time, "time", lambda: clock[0] / 1000)
    return clock


def test_a_carried_total_is_counted_again_after_five_minutes_so_lost_access_leaves_it(monkeypatch) -> None:
    clock = _clock(monkeypatch)
    provider = FakeProvider({"rg-1": [row("a1", "apple"), row("a3", "cherry")], "kb-1": [row("a2", "banana")]})
    first = asyncio.run(_search(provider, limit=2))
    assert first.total == 3
    provider.rows_by_connector["kb-1"] = []          # the viewer lost the banana

    clock[0] += 299_000
    provider.calls.clear()
    fresh = asyncio.run(_search(provider, limit=2, cursor_token=first.next_cursor))
    assert fresh.total == 3 and not any(c["include_total"] for c in provider.calls), "a recent total is reused"

    clock[0] += 2_000
    stale = asyncio.run(_search(provider, limit=2, cursor_token=fresh.prev_cursor))
    assert (stale.total, stale.counts_by_type) == (2, {"record": 2})


def test_a_recount_in_the_middle_of_a_walk_is_the_total_of_the_whole_listing(monkeypatch) -> None:
    """Not the rows past the page boundary, and over every connector, also one that ran out before the boundary."""
    clock = _clock(monkeypatch)
    provider = BoundaryCountingProvider({
        "rg-1": [row("a1", "apple"), row("a3", "cherry"), row("a4", "date", node_type="folder")], "kb-1": [row("a2", "banana")],
    })
    first = asyncio.run(_search(provider, limit=2))
    assert first.total == 4
    clock[0] += 301_000
    second = asyncio.run(_search(provider, limit=2, cursor_token=first.next_cursor))
    assert ids(second) == ["a3", "a4"]
    assert (second.total, second.counts_by_type) == (4, {"record": 3, "folder": 1})


def test_a_connector_not_ready_when_the_total_is_counted_again_refuses_the_cursor(monkeypatch) -> None:
    """The recount reads every connector, also one the page skips because it ran out: counted as 0 there, it would carry an understated total for five more minutes."""
    from app.exceptions.graph_db_exceptions import KnowledgeHubListingNotReady

    class NotReadyWhenCounted(FakeProvider):
        preparing: set[str] = set()

        async def get_knowledge_hub_connector_page_v3(self, *, app_id, **kwargs) -> dict:
            if app_id in self.preparing and kwargs.get("include_total"):
                raise KnowledgeHubListingNotReady
            return await super().get_knowledge_hub_connector_page_v3(app_id=app_id, **kwargs)

    clock = _clock(monkeypatch)
    provider = NotReadyWhenCounted({"rg-1": [row("a1", "apple"), row("a3", "cherry"), row("a4", "date")], "kb-1": [row("a2", "banana")]})
    first = asyncio.run(_search(provider, limit=2))
    second = asyncio.run(_search(provider, limit=2, cursor_token=first.next_cursor))
    assert ids(second) == ["a3", "a4"]          # kb-1 ran out on the first page: later pages do not read it
    provider.preparing = {"kb-1"}
    clock[0] += 301_000
    with pytest.raises(CursorError):
        asyncio.run(_search(provider, limit=2, cursor_token=second.prev_cursor))


@pytest.mark.parametrize("ca", [None, 0, -1, "soon", 1.5, True, [1], {"a": 1}])
def test_a_count_time_that_is_missing_or_not_a_time_is_an_unknown_age_and_the_total_is_counted_again(ca) -> None:
    wire = KnowledgeHubCursor(boundary=Boundary(0, "a", "a1"), total=5, counts_by_type={"record": 5}, counted_at_ms=1_000).to_wire()
    wire.pop("ca")
    if ca is not None:
        wire["ca"] = ca
    cursor = KnowledgeHubCursor.from_wire(wire)
    assert cursor.with_fresh_counts().total is None
    recent = KnowledgeHubCursor(boundary=Boundary(0, "a", "a1"), total=5, counted_at_ms=1_000)
    assert recent.with_fresh_counts(now_ms=1_000 + 300_000).total == 5
    assert recent.with_fresh_counts(now_ms=1_000 + 300_001).total is None


def test_a_failed_partition_fails_the_request() -> None:
    """A page of the partitions that answered is indistinguishable from a
    complete one, and the rows it drops look exactly like rows the user may not
    see.

    That a failing provider query raises instead of returning an empty page is
    per-provider and not covered here, because this drives a fake provider.
    """
    provider = FakeProvider(
        {"rg-1": [row("a1", "apple")], "rg-2": [row("a2", "banana")]},
        fails={"rg-2"},
    )
    with pytest.raises(BaseException) as caught:
        asyncio.run(_search(provider, limit=10))
    assert "rg-2" in str(caught.value) or any(
        "rg-2" in str(inner) for inner in getattr(caught.value, "exceptions", ())
    )


def test_partition_queries_are_capped() -> None:
    """A tenant can have a top-level group per user; the cap is what bounds a search."""
    provider = FakeProvider({f"rg-{index}": [row(f"a{index}", f"name {index:02d}")]
                             for index in range(30)})
    page = asyncio.run(_search(provider, limit=50, max_concurrency=4))
    assert len(page.rows) == 30, "every partition still contributes"
    assert provider.peak_in_flight <= 4, provider.peak_in_flight


def test_partition_queries_are_capped_by_default() -> None:
    from app.connectors.sources.localKB.handlers.kh_search import (
        DEFAULT_MAX_CONCURRENCY,
    )

    provider = FakeProvider({f"rg-{index}": [row(f"a{index}", f"name {index:02d}")]
                             for index in range(30)})
    page = asyncio.run(_search(provider, limit=50))
    assert len(page.rows) == 30
    assert DEFAULT_MAX_CONCURRENCY == 8
    assert 1 < provider.peak_in_flight <= DEFAULT_MAX_CONCURRENCY, provider.peak_in_flight


def test_an_exhausted_partition_is_not_queried_again() -> None:
    """The cursor's live set is what keeps a finished partition out."""
    provider = FakeProvider({
        "rg-1": [row("a1", "apple"), row("a4", "date")],
        "kb-1": [row("a2", "banana")],
    })
    first = asyncio.run(_search(provider, limit=2))
    assert ids(first) == ["a1", "a2"]
    provider.calls.clear()

    second = asyncio.run(_search(provider, limit=2, cursor_token=first.next_cursor))
    assert ids(second) == ["a4"]
    assert [call["app_id"] for call in provider.calls] == ["rg-1"]


def test_a_partition_gained_mid_walk_makes_the_page_query_them_all() -> None:
    """The bitset is positional, so a changed list invalidates it rather than shifting it.

    Skipping the stale bits is not a repair: the boundary still decides which
    rows come back, so the page stays exact — it just queries more partitions.
    """
    provider = FakeProvider({
        "rg-1": [row("a1", "apple"), row("a4", "date")],
        "kb-1": [row("a2", "banana")],
    })
    first = asyncio.run(_search(provider, limit=2))
    provider.rows_by_connector["rg-0"] = [row("a3", "cherry")]
    provider.calls.clear()

    second = asyncio.run(_search(provider, limit=2, cursor_token=first.next_cursor))
    assert ids(second) == ["a3", "a4"], "the new partition's row must not be skipped"
    assert len(provider.calls) == 3


def test_an_edited_cursor_cannot_name_a_partition_the_user_cannot_reach() -> None:
    """Positions are resolved against the list discovery just returned.

    There is no id in the cursor to edit, so the only reachable partitions are
    the ones this request discovered for this user.
    """
    provider = FakeProvider({"rg-1": [row("a1", "apple"), row("a2", "banana")]})
    first = asyncio.run(_search(provider, limit=1))
    second = asyncio.run(_search(provider, limit=1, cursor_token=first.next_cursor))
    assert [call["app_id"] for call in provider.calls
            if call["app_id"] != "rg-1"] == []
    assert ids(second) == ["a2"]


def test_a_previous_page_is_exactly_the_page_before_it() -> None:
    """Going back re-queries every partition: one exhausted going forward can
    still hold rows behind the boundary."""
    provider = FakeProvider({
        "rg-1": [row("a1", "apple"), row("a3", "cherry"), row("a5", "elder")],
        "rg-2": [row("a2", "banana"), row("a4", "date")],
    })
    first = asyncio.run(_search(provider, limit=2))
    second = asyncio.run(_search(provider, limit=2, cursor_token=first.next_cursor))
    third = asyncio.run(_search(provider, limit=2, cursor_token=second.next_cursor))
    assert [ids(first), ids(second), ids(third)] == [["a1", "a2"], ["a3", "a4"], ["a5"]]
    assert [(p.start_index, p.end_index) for p in (first, second, third)] == [(1, 2), (3, 4), (5, 5)]
    assert third.next_cursor is None

    provider.calls.clear()
    back = asyncio.run(_search(provider, limit=2, cursor_token=third.prev_cursor))
    assert ids(back) == ids(second)
    assert (back.start_index, back.end_index) == (3, 4)
    assert {call["app_id"] for call in provider.calls} == set(provider.rows_by_connector)


def test_a_page_ending_where_one_partition_ends_still_has_more() -> None:
    """Two partitions of two, limit 2. The first page empties rg-1
    exactly, which must not be mistaken for the walk being over."""
    provider = FakeProvider({
        "rg-1": [row("a1", "alpha"), row("a2", "bravo")],
        "rg-2": [row("a3", "charlie"), row("a4", "delta")],
    })
    first = asyncio.run(_search(provider, limit=2))
    second = asyncio.run(_search(provider, limit=2, cursor_token=first.next_cursor))

    assert [ids(first), ids(second)] == [["a1", "a2"], ["a3", "a4"]]
    assert first.next_cursor is not None and first.prev_cursor is None
    assert second.next_cursor is None and second.prev_cursor is not None


def test_a_page_that_exhausts_every_partition_offers_no_next_cursor() -> None:
    """One row per partition, limit 2 -- the page ends exactly as both
    partitions do.

    This is the boundary case the others cannot catch. An implementation that
    offered a next cursor whenever the page came back full would pass the other
    paging tests and fail only here, and the cursor it handed out would lead to
    an empty page.
    """
    provider = FakeProvider({
        "rg-1": [row("a1", "alpha")],
        "kb-1": [row("a2", "bravo")],
    })
    page = asyncio.run(_search(provider, limit=2))

    assert ids(page) == ["a1", "a2"]
    assert page.next_cursor is None, "a full page that exhausted everything is still the last"
    assert page.prev_cursor is None


def test_limit_one_switches_partition_every_page() -> None:
    """`[a1,a3]` and `[a2]` interleave, so at limit 1 each page comes
    from the other partition.

    The interleaving is the point -- with `[a1,a2]` in one partition a merge
    that concatenated partitions would walk the same three rows and pass. The
    backward step is the second half: `prev` from p3 must land on exactly
    `[a2]`, where an offset cursor would give `[a1]`.
    """
    provider = FakeProvider({
        "rg-1": [row("a1", "alpha"), row("a3", "charlie")],
        "kb-1": [row("a2", "bravo")],
    })
    first = asyncio.run(_search(provider, limit=1))
    second = asyncio.run(_search(provider, limit=1, cursor_token=first.next_cursor))
    third = asyncio.run(_search(provider, limit=1, cursor_token=second.next_cursor))

    assert [ids(first), ids(second), ids(third)] == [["a1"], ["a2"], ["a3"]]
    assert third.next_cursor is None

    back = asyncio.run(_search(provider, limit=1, cursor_token=third.prev_cursor))
    assert ids(back) == ["a2"], "the step back must be one row, not one partition"


def test_the_first_page_has_no_previous_cursor() -> None:
    provider = FakeProvider({"rg-1": [row("a1", "apple"), row("a2", "banana")]})
    first = asyncio.run(_search(provider, limit=1))
    assert first.prev_cursor is None and first.next_cursor is not None
    assert asyncio.run(_search(provider, limit=1, cursor_token=first.next_cursor)).prev_cursor


def test_the_cursor_decides_sort_and_filters() -> None:
    """A page that honoured a conflicting sort would resume a keyset
    from an order that no longer applies, and silently skip rows."""
    provider = FakeProvider({
        "rg-1": [row("a1", "apple"), row("a2", "banana"), row("a3", "cherry")],
    })
    first = asyncio.run(_search(
        provider, limit=1, sort_field="createdAt", sort_dir="DESC",
        filters={"search_query": "hr", "record_types": ["FILE"]},
    ))
    provider.calls.clear()

    asyncio.run(_search(
        provider, limit=1, cursor_token=first.next_cursor,
        sort_field="name", sort_dir="ASC", filters={"search_query": "other"},
    ))
    call = provider.calls[0]
    assert (call["sort_field"], call["sort_dir"]) == ("createdAt", "DESC")
    assert call["filters"]["search_query"] == "hr"
    assert call["filters"]["record_types"] == ["FILE"]


def test_another_users_cursor_is_refused() -> None:
    """The caller answers 400; it is never treated as a first page."""
    provider = FakeProvider({"rg-1": [row("a1", "apple"), row("a2", "banana")]})
    first = asyncio.run(_search(provider, limit=1))
    with pytest.raises(CursorError):
        asyncio.run(search_page(
            provider, user_key="v-key", user_id="user-v", org_id=ORG,
            limit=1, cursor_token=first.next_cursor, secret=SECRET,
        ))


def test_access_lost_between_pages_takes_effect_on_the_next_page() -> None:
    """A gap or a duplicate is accepted; an item the user may not see is not."""
    provider = FakeProvider({
        "rg-1": [row("a1", "apple"), row("a3", "cherry"), row("a5", "elder")],
        "rg-2": [row("a2", "banana"), row("a4", "date")],
    })
    first = asyncio.run(_search(provider, limit=2))
    del provider.rows_by_connector["rg-2"]

    second = asyncio.run(_search(provider, limit=2, cursor_token=first.next_cursor))
    assert ids(second) == ["a3", "a5"], "no row from the revoked partition"


def test_a_search_matching_nothing_returns_an_empty_page() -> None:
    """No rows, no cursors, and a total of zero rather than None."""
    page = asyncio.run(_search(FakeProvider({"rg-1": []}), limit=10))
    assert page.rows == [] and page.total == 0 and page.counts_by_type == {}
    assert page.next_cursor is None and page.prev_cursor is None
    assert (page.start_index, page.end_index) == (0, 0)


class RefusingProvider(FakeProvider):
    def __init__(self, rows, error) -> None:
        super().__init__(rows)
        self.error = error

    async def get_knowledge_hub_connector_page_v3(self, *, app_id, **kwargs) -> dict:
        if app_id == "big":
            raise self.error
        return await super().get_knowledge_hub_connector_page_v3(app_id=app_id, **kwargs)


@pytest.mark.parametrize("error", [KnowledgeHubListingTooLarge(), KnowledgeHubListingBusy()])
def test_one_connector_refusing_fails_the_page_as_itself_not_as_a_group(error) -> None:
    provider = RefusingProvider({"big": [row("b1", "B")], "small": [row("s1", "S")]}, error)
    with pytest.raises(type(error)):
        asyncio.run(_search(provider, limit=10))
