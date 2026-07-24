"""
Dynamic filter-option pickers for the GitHub Teams connector.

Responsibilities:
- ``get_filter_options``: public entry point for the org/repo picker fields.
- ``_org_filter_options``: org picker (``list_user_orgs`` + local search).
- ``_repo_filter_options``: repo picker (``search_repositories`` when searching,
  ``list_org_repos``/``list_user_repos`` otherwise).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.connectors.core.registry.filters import FilterOption, FilterOptionsResponse, SyncFilterKey

from .constants import _FILTER_OPTIONS_MAX_PER_PAGE as _MAX_PER_PAGE

if TYPE_CHECKING:
    from app.connectors.sources.github_teams.connector import GitHubTeamsConnector


def _clamp_per_page(limit: int) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 20
    if n <= 0:
        n = 20
    return min(n, _MAX_PER_PAGE)


class FiltersHelper:
    """Dynamic org/repo filter-option provider for ``GitHubTeamsConnector``."""

    def __init__(self, connector: "GitHubTeamsConnector") -> None:
        self.c = connector
        self.logger = connector.logger

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: str | None = None,
        cursor: str | None = None,
    ) -> FilterOptionsResponse:
        c = self.c
        await c.runtime.refresh_token_if_needed()
        if not c.data_source:
            return FilterOptionsResponse(
                success=False, options=[], page=page, limit=limit,
                has_more=False, message="GitHub connector not initialized",
            )
        try:
            if filter_key == SyncFilterKey.ORG_IDS.value:
                return await self._org_filter_options(page, limit, search)
            if filter_key == SyncFilterKey.REPO_IDS.value:
                return await self._repo_filter_options(page, limit, search)
            raise ValueError(f"Unsupported filter key: {filter_key}")
        except ValueError:
            raise
        except Exception as e:
            self.logger.error("get_filter_options failed for %s: %s", filter_key, e, exc_info=True)
            return FilterOptionsResponse(
                success=False, options=[], page=page, limit=limit, has_more=False, message=str(e),
            )

    # ------------------------------------------------------------------
    # Org picker
    # ------------------------------------------------------------------

    async def _org_filter_options(self, page: int, limit: int, search: str | None) -> FilterOptionsResponse:
        c = self.c
        per_page = _clamp_per_page(limit)
        res = await c.runtime.ds_call(c.data_source.list_user_orgs)
        if not res.success:
            self.logger.warning("list_user_orgs failed for filter options: %s", res.error)
            return FilterOptionsResponse(success=False, options=[], page=page, limit=limit, has_more=False, message=res.error)

        orgs = list(res.data or [])
        if search:
            needle = search.casefold()
            orgs = [
                o for o in orgs
                if needle in (getattr(o, "login", "") or "").casefold()
                or needle in (getattr(o, "name", "") or "").casefold()
            ]
        orgs.sort(key=lambda o: (getattr(o, "login", "") or "").casefold())

        start = (max(1, int(page)) - 1) * per_page
        end = start + per_page
        page_items = orgs[start:end]
        has_more = len(orgs) > end

        opts = [
            FilterOption(id=str(o.login), label=str(getattr(o, "name", None) or o.login))
            for o in page_items
        ]
        return FilterOptionsResponse(success=True, options=opts, page=page, limit=limit, has_more=has_more)

    # ------------------------------------------------------------------
    # Repo picker
    # ------------------------------------------------------------------

    async def _repo_filter_options(self, page: int, limit: int, search: str | None) -> FilterOptionsResponse:
        c = self.c
        per_page = _clamp_per_page(limit)
        page_n = max(1, int(page))

        if search:
            res = await c.runtime.ds_call(
                c.data_source.search_repositories, f"{search} in:name", per_page, page_n,
            )
            if not res.success:
                self.logger.warning("search_repositories failed for filter options (search=%r): %s", search, res.error)
                return FilterOptionsResponse(success=False, options=[], page=page, limit=limit, has_more=False, message=res.error)
            repos = list(res.data or [])
            has_more = len(repos) >= per_page
        else:
            res = await c.runtime.ds_call(c.data_source.list_user_repos, None, "all")
            if not res.success:
                self.logger.warning("list_user_repos failed for filter options: %s", res.error)
                return FilterOptionsResponse(success=False, options=[], page=page, limit=limit, has_more=False, message=res.error)
            all_repos = list(res.data or [])
            all_repos.sort(key=lambda r: (getattr(r, "full_name", "") or "").casefold())
            start = (page_n - 1) * per_page
            end = start + per_page
            repos = all_repos[start:end]
            has_more = len(all_repos) > end

        opts = [FilterOption(id=str(r.full_name), label=str(r.full_name)) for r in repos]
        return FilterOptionsResponse(success=True, options=opts, page=page, limit=limit, has_more=has_more)
