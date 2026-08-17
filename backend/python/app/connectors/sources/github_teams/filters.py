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

from .constants import (
    _FILTER_OPTIONS_MAX_PER_PAGE as _MAX_PER_PAGE,
    _FILTER_OPTIONS_MAX_SCAN_PAGES as _MAX_SCAN_PAGES,
)

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
            FilterOption(id=login, label=str(getattr(o, "name", None) or login))
            for o in page_items
            if (login := str(getattr(o, "login", "") or ""))
        ]
        return FilterOptionsResponse(success=True, options=opts, page=page, limit=per_page, has_more=has_more)

    # ------------------------------------------------------------------
    # Repo picker
    # ------------------------------------------------------------------

    @staticmethod
    def _request_context(connector: object, attr: str) -> list[str]:
        """Org logins for the selection the admin is editing right now.

        Set per request by the filter-options route from the picker's
        ``contextGroupPath`` query param, and removed again afterwards.
        """
        return [
            str(p).strip()
            for p in (getattr(connector, attr, None) or [])
            if p and str(p).strip()
        ]

    async def _scope_orgs(self) -> tuple[list[str], bool]:
        """Orgs the repo picker may offer, as ``(orgs, resolved_ok)``.

        The in-flight selection wins over saved sync filters: orgs and repos
        are chosen in one sitting, so the org rows just ticked in the UI have
        not been persisted yet and ``sync_filters`` still holds the previous
        selection.
        """
        c = self.c
        include = self._request_context(c, "_request_filter_context_group_paths")
        if include:
            return include, True

        orgs, ok = await c.users._resolve_target_orgs()
        if not ok:
            return [], False

        exclude = {
            org.casefold()
            for org in self._request_context(c, "_request_filter_context_exclude_group_paths")
        }
        if exclude:
            orgs = [org for org in orgs if org.casefold() not in exclude]
        return orgs, True

    async def _repo_filter_options(self, page: int, limit: int, search: str | None) -> FilterOptionsResponse:
        c = self.c
        per_page = _clamp_per_page(limit)
        page_n = max(1, int(page))

        scope_orgs, ok = await self._scope_orgs()
        if not ok:
            return FilterOptionsResponse(
                success=False, options=[], page=page, limit=limit, has_more=False,
                message="Could not list GitHub organizations for this token.",
            )

        repos, has_more, error = (
            await self._search_scoped_repos(scope_orgs, search, per_page, page_n)
            if search
            else await self._list_scoped_repos(scope_orgs, per_page, page_n)
        )
        if error is not None:
            return FilterOptionsResponse(
                success=False, options=[], page=page, limit=limit, has_more=False, message=error,
            )

        opts = [
            FilterOption(id=full_name, label=full_name)
            for r in repos
            if (full_name := str(getattr(r, "full_name", "") or ""))
        ]
        return FilterOptionsResponse(success=True, options=opts, page=page, limit=per_page, has_more=has_more)

    async def _search_scoped_repos(
        self, scope_orgs: list[str], search: str, per_page: int, page_n: int,
    ) -> tuple[list, bool, str | None]:
        """Search the in-scope orgs first, then the rest of public GitHub.

        The scoped pass runs first so a user's own repositories always rank
        above same-named public ones. The public pass only runs when the scoped
        one leaves room on the page, which keeps the common case at a single
        Search API call — that pool is only 30 req/min.

        Public repos are offered because they now sync correctly: a repo whose
        collaborator listing is refused (that endpoint needs push access) falls
        back to its visibility floor, and for a public repo `Permission(READ,
        ORG)` is the complete, accurate ACL.
        """
        c = self.c
        qualifiers = [f"org:{org}" for org in scope_orgs]
        login = getattr(c, "_github_login", None)
        if login:
            qualifiers.append(f"user:{login}")

        # Over-fetch one row so has_more reflects a further row existing,
        # rather than this page merely filling up.
        fetch_per_page = per_page + 1
        scoped: list = []
        scoped_has_more = False
        if qualifiers:
            scoped, scoped_has_more, error = await self._run_repo_search(
                f"{search} in:name " + " ".join(qualifiers), fetch_per_page, page_n, search,
            )
            if error is not None:
                return [], False, error
            if len(scoped) > per_page:
                return scoped[:per_page], True, None

        seen = {str(getattr(r, "full_name", "") or "").casefold() for r in scoped}
        public, public_has_more, error = await self._run_repo_search(
            f"{search} in:name", fetch_per_page, page_n, search,
        )
        if error is not None:
            # The scoped rows are still useful; surface them rather than failing
            # the whole picker on a public-search hiccup.
            return scoped[:per_page], scoped_has_more, None
        merged = scoped + [
            r for r in public
            if (fn := str(getattr(r, "full_name", "") or "").casefold()) and fn not in seen
        ]
        return merged[:per_page], len(merged) > per_page or public_has_more, None

    async def _run_repo_search(
        self, query: str, fetch_per_page: int, page_n: int, search: str,
    ) -> tuple[list, bool, str | None]:
        res = await self.c.runtime.search_call(
            self.c.data_source.search_repositories, query, per_page=fetch_per_page, page=page_n,
        )
        if not res.success:
            self.logger.warning(
                "search_repositories failed for filter options (search=%r): %s", search, res.error
            )
            return [], False, res.error
        raw = list(res.data or [])
        return raw, len(raw) > fetch_per_page - 1, None

    async def _list_scoped_repos(
        self, scope_orgs: list[str], per_page: int, page_n: int,
    ) -> tuple[list, bool, str | None]:
        """Repos across the in-scope orgs (browse mode, no search text).

        The token owner's own repos are offered only when there is no org
        scope; with orgs in scope they surface via search (the ``user:``
        qualifier), not here. Multi-org results are merged and sorted locally
        because GitHub cannot page across orgs.
        """
        c = self.c

        # A single source can be paged by GitHub directly — no local slicing.
        if len(scope_orgs) == 1:
            return await self._single_source_page(
                c.data_source.list_org_repos, (scope_orgs[0], "all"), per_page, page_n,
                label=f"list_org_repos(org={scope_orgs[0]})",
            )
        if not scope_orgs:
            # No org scope: the picker offers the token owner's own repos.
            return await self._single_source_page(
                c.data_source.list_user_repos, (None, "owner"), per_page, page_n,
                label="list_user_repos(owner)",
            )

        # Several orgs: results must be merged into one sorted list, which
        # GitHub cannot do across orgs, so paging is local. Bounded by
        # _MAX_SCAN_PAGES per org and stopped as soon as the requested page
        # can be filled, so it never walks every repo the user can see.
        target_count = page_n * per_page + 1
        by_full_name: dict[str, object] = {}
        any_success = False
        last_error: str | None = None

        for org in scope_orgs:
            for upstream_page in range(1, _MAX_SCAN_PAGES + 1):
                res = await c.runtime.ds_call(
                    c.data_source.list_org_repos, org, "all",
                    per_page=_MAX_PER_PAGE, page=upstream_page,
                )
                if not res.success:
                    self.logger.warning("list_org_repos failed for filter options (org=%s): %s", org, res.error)
                    last_error = res.error
                    break
                any_success = True
                items = list(res.data or [])
                for r in items:
                    if name := str(getattr(r, "full_name", "") or ""):
                        by_full_name[name] = r
                if len(items) < _MAX_PER_PAGE:
                    break
                if len(by_full_name) >= target_count:
                    self.logger.debug(
                        "Repo picker: stopped scanning %s at page %s (have %s, need %s)",
                        org, upstream_page, len(by_full_name), target_count,
                    )
                    break

        if not any_success:
            # An empty list is only trustworthy if something actually listed.
            return [], False, last_error or "Could not list GitHub repositories."

        ordered = [by_full_name[k] for k in sorted(by_full_name, key=str.casefold)]
        start = (page_n - 1) * per_page
        end = start + per_page
        return ordered[start:end], len(ordered) > end, None

    async def _single_source_page(
        self, method: object, args: tuple, per_page: int, page_n: int, label: str,
    ) -> tuple[list, bool, str | None]:
        """One page from a single listing endpoint, paged by GitHub.

        Over-fetches one row so ``has_more`` reflects a further row existing
        rather than this page merely filling up.
        """
        res = await self.c.runtime.ds_call(
            method, *args, per_page=per_page + 1, page=page_n,
        )
        if not res.success:
            self.logger.warning("%s failed for filter options: %s", label, res.error)
            return [], False, res.error
        raw = list(res.data or [])
        return raw[:per_page], len(raw) > per_page, None
