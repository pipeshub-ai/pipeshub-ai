"""
Finding issues that left a repository and removing them from PipesHub.

GitHub has no feed of deleted or transferred issues, and the issues listing simply stops
returning them. Re-reading every issue on every sync would cost one call per 100 issues,
so the check is two-staged:

1. One GraphQL query reads the repository's issue total. It is compared with the total
   the last sync left plus the issues created since. Equal means nothing left.
2. Only on a mismatch, every issue number is read through GraphQL's cursor pagination
   (a keyset, so an issue changing during the sweep cannot hide another), and each
   stored ticket missing from it is asked for directly: 410 or 404, or a redirect to
   another repository, is a definite answer that it is gone. Anything else keeps it.

The same sweep also finds issues the listing missed. The listing is read newest first by
offset, so an issue leaving it between two page requests (deleted, transferred, made a
discussion) moves the next page up one place, and the issue at the boundary is never read.
That is exactly when the totals disagree, so on a sweep every issue GitHub shows as
changed after its stored copy, or not stored at all, is fetched again through ``refresh``.

``reconcile`` returns None whenever it could not get a definite answer; nothing is
removed then.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from app.models.entities import Record, RecordType

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from logging import Logger

    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    from app.sources.external.github.github_async import GhObject, GitHubAsyncDataSource

    from .record_removal import RecordRemover
    from .runtime import RuntimeHelper

_TOTAL_QUERY = (
    "query($owner: String!, $name: String!) "
    "{ repository(owner: $owner, name: $name) { issues { totalCount } } }"
)
_NUMBERS_QUERY = (
    "query($owner: String!, $name: String!, $cursor: String) "
    "{ repository(owner: $owner, name: $name) { issues(first: 100, after: $cursor) "
    "{ pageInfo { hasNextPage endCursor } nodes { number updatedAt } } } }"
)
_GONE_STATUSES = (404, 410)
_STORED_PAGE = 500


def _epoch_ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


class IssueReconciler:
    def __init__(
        self,
        runtime: "RuntimeHelper",
        data_source: Callable[[], "GitHubAsyncDataSource"],
        processor: DataSourceEntitiesProcessor,
        remover: RecordRemover,
        connector_id: str,
        logger: Logger,
    ) -> None:
        self._runtime = runtime
        self._data_source = data_source
        self._processor = processor
        self._remover = remover
        self._connector_id = connector_id
        self._logger = logger

    async def source_total(self, owner: str, name: str) -> int | None:
        res = await self._runtime.ds_call(
            self._data_source().graphql_query, _TOTAL_QUERY, {"owner": owner, "name": name}
        )
        try:
            return int(res.data["repository"]["issues"]["totalCount"]) if res.success else None
        except (KeyError, TypeError, ValueError):
            return None

    async def reconcile(
        self,
        repo: GhObject,
        external_group_id: str,
        refresh: Callable[[list[GhObject]], Awaitable[bool]],
        since_ms: int | None = None,
    ) -> int | None:
        """Remove stored tickets that left ``repo`` and ``refresh`` the ones the listing
        missed (changed after ``since_ms``, the start of the sync window, if any); return
        GitHub's issue count, or None when the answer was not definite (nothing was
        removed then)."""
        owner, name = repo.owner.login, repo.name
        numbers = await self._source_numbers(owner, name)
        if numbers is None:
            return None
        stored = await self._stored_tickets(repo.id, external_group_id)
        behind = [
            n for n, updated in numbers.items()
            if (n in stored and (stored[n].source_updated_at or 0) // 1000 < updated // 1000)
            or (n not in stored and (since_ms is None or updated >= since_ms))
        ]
        if behind and not await self._refresh(owner, name, behind, refresh):
            return None
        gone: list[Record] = []
        for number, record in stored.items():
            if number in numbers:
                continue
            verdict = await self._is_gone(owner, name, number)
            if verdict is None:
                return None
            if verdict:
                gone.append(record)
        if gone:
            await self._remover.remove_records(gone)
            self._logger.info(
                "Removed %s issue(s) deleted from or transferred out of %s", len(gone), repo.full_name
            )
        return len(numbers)

    async def remove_all(self, repo: GhObject, external_group_id: str) -> None:
        """Remove every stored ticket of ``repo``: its issues are turned off, so GitHub
        hides them all (and shows them again when they are turned back on)."""
        stored = await self._stored_tickets(repo.id, external_group_id)
        if stored:
            await self._remover.remove_records(list(stored.values()))
            self._logger.info("Issues are turned off for %s; removed %s issue(s)", repo.full_name, len(stored))

    async def _refresh(
        self, owner: str, name: str, numbers: list[int],
        refresh: Callable[[list[GhObject]], Awaitable[bool]],
    ) -> bool:
        issues: list[GhObject] = []
        for number in sorted(numbers):
            res = await self._runtime.ds_call(self._data_source().get_issue, owner, name, number)
            if not res.success or res.data is None:
                self._logger.warning("Could not re-read issue %s/%s#%s the listing missed: %s",
                                     owner, name, number, res.error)
                return False
            # A redirect lands on an issue transferred elsewhere; it is not this repository's.
            if str(getattr(res.data, "html_url", "") or "").lower().endswith(f"/{owner}/{name}/issues/{number}".lower()):
                issues.append(res.data)
        self._logger.info("Re-reading %s issue(s) of %s/%s the listing missed", len(issues), owner, name)
        return not issues or await refresh(issues)

    async def _source_numbers(self, owner: str, name: str) -> dict[int, int] | None:
        """Issue number -> last update (epoch ms), every issue of the repository."""
        numbers: dict[int, int] = {}
        cursor: str | None = None
        while True:
            res = await self._runtime.ds_call(
                self._data_source().graphql_query, _NUMBERS_QUERY,
                {"owner": owner, "name": name, "cursor": cursor},
            )
            try:
                issues = res.data["repository"]["issues"] if res.success else None
                numbers.update(
                    (int(n["number"]), _epoch_ms(n["updatedAt"])) for n in issues["nodes"]
                )
                page = issues["pageInfo"]
            except (KeyError, TypeError, ValueError):
                self._logger.warning(
                    "Could not list the issues of %s/%s to look for removed ones: %s",
                    owner, name, getattr(res, "error", "malformed response"),
                )
                return None
            if not page.get("hasNextPage"):
                return numbers
            if not page.get("endCursor") or page.get("endCursor") == cursor:
                # A walk that does not move would spend the whole GraphQL budget.
                self._logger.warning("The issue listing of %s/%s did not advance; not checking removals", owner, name)
                return None
            cursor = page.get("endCursor")

    async def _stored_tickets(self, repo_id: int, external_group_id: str) -> dict[int, Record]:
        prefix = f"{repo_id}/issues/"
        out: dict[int, Record] = {}
        after: str | None = None
        while True:
            page = await self._processor.get_records_in_record_group(
                self._connector_id, external_group_id, _STORED_PAGE, after
            )
            for record in page:
                external_id = str(record.external_record_id or "")
                if (record.record_type == RecordType.TICKET.value and external_id.startswith(prefix)
                        and external_id[len(prefix):].isdigit()):
                    out[int(external_id[len(prefix):])] = record
            if len(page) < _STORED_PAGE:
                return out
            after = page[-1].id

    async def _is_gone(self, owner: str, name: str, number: int) -> bool | None:
        res = await self._runtime.ds_call(self._data_source().get_issue, owner, name, number)
        if res.success and res.data is not None:
            # A transferred issue answers 301; the redirect lands on its new home.
            html_url = str(getattr(res.data, "html_url", "") or "").lower()
            return not html_url.endswith(f"/{owner}/{name}/issues/{number}".lower())
        if res.status_code in _GONE_STATUSES:
            return True
        return None
