"""Listing issues or merge requests by update time without losing any to concurrent edits.

GitLab's ``page=N`` is an offset. In an ``updated_at asc`` listing an item edited between
two page requests moves to the end, and one deleted disappears; either way every later
item moves up a place and the one that crosses the page boundary is never read. The
checkpoint then moves past it, so it is not read again until it changes.

Here every request asks again from the last update time already read, so a move or a
removal cannot shift anything past the reader. GitLab's ``updated_after`` is inclusive,
so the items at that exact time come back and are dropped by id.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from itertools import islice
from typing import TYPE_CHECKING, Any

from app.sources.client.gitlab.gitlab import GitLabResponse

if TYPE_CHECKING:
    from collections.abc import Callable

    from .runtime import RuntimeHelper

_PAGE_SIZE = 100
# GitLab returns update times to the millisecond; the stored value can be finer.
_ONE_MS = timedelta(milliseconds=1)


def _updated(item: object) -> datetime:
    """At GitLab's millisecond precision (``parse_timestamp`` keeps whole seconds)."""
    value = datetime.fromisoformat(str(item.updated_at).replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class UpdateOrderedListing:
    """Reads every item updated in ``[updated_after, updated_before]``, oldest change first."""

    def __init__(self, runtime: "RuntimeHelper", page_size: int = _PAGE_SIZE) -> None:
        self.runtime = runtime
        self.page_size = page_size

    async def list(
        self,
        method: Callable[..., GitLabResponse],
        *,
        project_id: int,
        updated_after: datetime | None,
        updated_before: datetime | None,
        created_after: datetime | None,
        created_before: datetime | None,
    ) -> GitLabResponse:
        """``success=False`` with what was read so far when a page fails."""
        by_id: dict[int, Any] = {}
        cursor = updated_after

        async def page(after: datetime | None, before: datetime | None, number: int) -> tuple[GitLabResponse, bool]:
            """One page, and whether GitLab says another follows (``x-next-page``): the
            server may cap a page below the size asked for."""
            res = await self.runtime.ds_call(
                method,
                project_id=project_id,
                updated_after=after,
                updated_before=before,
                created_after=created_after,
                created_before=created_before,
                order_by="updated_at",
                sort="asc",
                per_page=self.page_size,
                page=number,
                iterator=True,
            )
            if not res.success or res.data is None:
                return res, False
            gl_list = res.data
            # Only the page already fetched: islice stops before the list asks for the next one.
            items = list(islice(gl_list, gl_list.per_page or self.page_size))
            return GitLabResponse(success=True, data=items), gl_list.next_page is not None

        def failed(res: GitLabResponse) -> GitLabResponse:
            return GitLabResponse(success=False, data=list(by_id.values()), error=res.error,
                                  status_code=res.status_code)

        while True:
            res, more = await page(cursor, updated_before, 1)
            if not res.success:
                return failed(res)
            items = list(res.data or [])
            for item in items:
                by_id[item.id] = item
            if not more or not items:
                break
            last = _updated(items[-1])
            if cursor is None or last > cursor:
                cursor = last
                continue
            # A whole page changed in the same millisecond: read that millisecond by
            # offset (it cannot move: anything edited now leaves it), then step past it.
            instant_end = last + _ONE_MS - timedelta(microseconds=1)
            number = 2
            while more:
                res, more = await page(last, instant_end, number)
                if not res.success:
                    return failed(res)
                for item in res.data or []:
                    by_id[item.id] = item
                number += 1
            cursor = last + _ONE_MS
            if updated_before is not None and cursor > updated_before:
                break
        ordered = sorted(by_id.values(), key=lambda i: (_updated(i), i.id))
        return GitLabResponse(success=True, data=ordered)
