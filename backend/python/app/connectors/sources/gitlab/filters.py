"""
Filter-options helpers for the GitLab connector.

Responsibilities:
- ``get_filter_options``: public entry point for group/project picker options.
- ``_gitlab_group_filter_options``: group picker with auditor fallback.
- ``_gitlab_project_filter_options``: project picker scoped or unscoped.
- ``_scan_filter_option_pages``: multi-page local-match scanner.
- ``_gitlab_group_picker_auditor_fallback``: auditor-role group expansion.
- Static helpers: ``_clamp_per_page``, ``_is_short_search``, ``_local_match_group``,
  ``_local_match_project``, ``_short_search_filter_options_response``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable

from app.connectors.core.registry.filters import FilterOption, FilterOptionsResponse

from .constants import (
    GITLAB_SEARCH_MIN_PARTIAL_CHARS,
    _FILTER_OPTIONS_MAX_PER_PAGE as _MAX_PER_PAGE,
    _FILTER_OPTIONS_MAX_SCAN_PAGES as _MAX_SCAN_PAGES,
)
from .models import GitlabLiterals

if TYPE_CHECKING:
    from app.connectors.sources.gitlab.connector import GitLabConnector


class FiltersHelper:
    """Dynamic group/project filter-option provider for ``GitLabConnector``."""

    def __init__(self, connector: "GitLabConnector") -> None:
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
        """Return dynamic options for the GitLab group and project filters."""
        c = self.c
        await c.runtime.refresh_token_if_needed()
        if not c.data_source:
            return FilterOptionsResponse(
                success=False, options=[], page=page, limit=limit,
                has_more=False, message="GitLab connector not initialized",
            )
        try:
            from app.connectors.core.registry.filters import SyncFilterKey
            if filter_key == SyncFilterKey.GROUP_IDS.value:
                return await self._gitlab_group_filter_options(page, limit, search)
            if filter_key == SyncFilterKey.PROJECT_IDS.value:
                return await self._gitlab_project_filter_options(page, limit, search)
            raise ValueError(f"Unsupported filter key: {filter_key}")
        except ValueError:
            raise
        except Exception as e:
            self.logger.error("get_filter_options failed for %s: %s", filter_key, e, exc_info=True)
            return FilterOptionsResponse(
                success=False, options=[], page=page, limit=limit, has_more=False, message=str(e),
            )

    # ------------------------------------------------------------------
    # Group picker
    # ------------------------------------------------------------------

    async def _gitlab_group_filter_options(
        self, page: int, limit: int, search: str | None
    ) -> FilterOptionsResponse:
        c = self.c
        search = search or None
        per_page = _clamp_per_page(limit)
        too_short = _is_short_search(search)
        if too_short:
            return _short_search_filter_options_response(page, limit)

        server_search = search
        list_kwargs: dict[str, object] = {
            "search": server_search,
            "get_all": False,
            **c.scope.list_groups_scope_kwargs(),
        }
        if not server_search:
            list_kwargs["order_by"] = "path"
            list_kwargs["sort"] = "asc"

        if search:
            needle = search.casefold()
            groups, has_more, error = await self._scan_filter_option_pages(
                c.data_source.list_groups,
                list_kwargs=list_kwargs,
                matcher=lambda g: _local_match_group(g, needle),
                page=page, per_page=per_page,
                progress_label="GitLab group filter search",
            )
            if error:
                self.logger.warning("GitLab list_groups failed for filter options (search=%r, page=%s): %s", search, page, error)
                return FilterOptionsResponse(success=False, options=[], page=page, limit=limit, has_more=False, message=error)
            if c._is_auditor and not groups:
                groups, has_more, error = await self._gitlab_group_picker_auditor_fallback(
                    list_kwargs=list_kwargs, needle=needle, page=page, per_page=per_page,
                )
                if error:
                    self.logger.warning("GitLab list_groups auditor fallback failed (search=%r, page=%s): %s", search, page, error)
        else:
            list_kwargs["page"] = max(1, int(page))
            list_kwargs["per_page"] = per_page + 1
            res = await c.runtime.ds_call(c.data_source.list_groups, **list_kwargs)
            if not res.success:
                self.logger.warning("GitLab list_groups failed for filter options (search=%r, page=%s): %s", search, page, res.error)
                return FilterOptionsResponse(success=False, options=[], page=page, limit=limit, has_more=False, message=res.error)
            groups = list(res.data or [])
            has_more = len(groups) > per_page
            if has_more:
                groups = groups[:per_page]
            if c._is_auditor and not groups:
                groups, has_more, error = await self._gitlab_group_picker_auditor_fallback(
                    list_kwargs=list_kwargs, needle=None, page=page, per_page=per_page,
                )
                if error:
                    self.logger.warning("GitLab list_groups auditor fallback failed (search=%r, page=%s): %s", search, page, error)

        opts = [
            FilterOption(
                id=str(g.full_path),
                label=str(getattr(g, "full_name", None) or g.full_path or g.name),
            )
            for g in groups
        ]
        return FilterOptionsResponse(success=True, options=opts, page=page, limit=limit, has_more=has_more)

    async def _gitlab_group_picker_auditor_fallback(
        self, *, list_kwargs: dict[str, object], needle: str | None, page: int, per_page: int,
    ) -> tuple[list[Any], bool, str | None]:
        """Retry group listing with membership scope and descendant expansion for auditor users."""
        c = self.c
        c.scope.warn_auditor_fallback_once("groups")
        fallback_kwargs = dict(c.scope.picker_kwargs_for_auditor_groups_fallback(list_kwargs))
        for k in ("page", "per_page", "get_all"):
            fallback_kwargs.pop(k, None)
        base_res = await c.runtime.paged_list(
            c.data_source.list_groups,
            progress_label="GitLab group filter [auditor fallback]",
            **fallback_kwargs,
        )
        if not base_res.success:
            return [], False, base_res.error
        base_groups = list(base_res.data or [])
        merged = await c.scope.expand_groups_with_descendants(
            base_groups, progress_label="GitLab group filter [auditor descendants]"
        )
        if needle:
            merged = [g for g in merged if _local_match_group(g, needle)]
        merged.sort(key=lambda g: (getattr(g, "full_path", "") or "").casefold())
        start = (max(1, int(page)) - 1) * per_page
        end = start + per_page
        return merged[start:end], len(merged) > end, None

    # ------------------------------------------------------------------
    # Project picker
    # ------------------------------------------------------------------

    async def _gitlab_project_filter_options(
        self, page: int, limit: int, search: str | None
    ) -> FilterOptionsResponse:
        c = self.c
        search = search or None
        scope_paths: list[str] = [
            p for p in (getattr(c, "_request_filter_context_group_paths", None) or [])
            if p and str(p).strip()
        ]
        exclude_paths: list[str] = [
            p for p in (getattr(c, "_request_filter_context_exclude_group_paths", None) or [])
            if p and str(p).strip()
        ]
        per_page = _clamp_per_page(limit)
        page_n = max(1, int(page))

        if _is_short_search(search):
            return _short_search_filter_options_response(page, limit)

        if scope_paths and c.data_source:
            return await self._scoped_project_filter_options(scope_paths, search, per_page, page_n, limit)
        return await self._unscoped_project_filter_options(search, exclude_paths, per_page, page_n, limit, page)

    async def _scoped_project_filter_options(
        self, scope_paths: list[str], search: str | None, per_page: int, page_n: int, limit: int,
    ) -> FilterOptionsResponse:
        c = self.c
        by_id: dict[int, Any] = {}
        any_has_more = False

        if search:
            needle = search.casefold()

            async def _fetch_matches(gp: str) -> tuple[str, list[Any]]:
                group_matches: list[Any] = []
                upstream_page = 1
                while True:
                    gp_kwargs: dict[str, object] = {
                        "include_subgroups": True, "get_all": False, "page": upstream_page,
                        "per_page": _MAX_PER_PAGE, "simple": True, "order_by": "path", "sort": "asc",
                    }
                    gres = await c.runtime.ds_call(c.data_source.list_group_projects, gp, **gp_kwargs)
                    if not gres.success:
                        self.logger.warning("Could not list projects for group %s (filter options): %s", gp, gres.error)
                        break
                    items = list(gres.data or [])
                    group_matches.extend(p for p in items if _local_match_project(p, needle))
                    if len(items) < _MAX_PER_PAGE or upstream_page >= _MAX_SCAN_PAGES:
                        break
                    upstream_page += 1
                return gp, group_matches

            results = await asyncio.gather(*(_fetch_matches(gp) for gp in scope_paths))
            for _gp, matches in results:
                for p in matches:
                    by_id[int(p.id)] = p
        else:
            async def _fetch(gp: str) -> tuple[str, Any]:
                gp_kwargs: dict[str, object] = {
                    "include_subgroups": True, "get_all": False, "page": page_n,
                    "per_page": per_page + 1, "simple": True, "order_by": "path", "sort": "asc",
                }
                gres = await c.runtime.ds_call(c.data_source.list_group_projects, gp, **gp_kwargs)
                return gp, gres

            results = await asyncio.gather(*(_fetch(gp) for gp in scope_paths))
            for gp, gres in results:
                if not gres.success:
                    self.logger.warning("Could not list projects for group %s (filter options): %s", gp, gres.error)
                    continue
                items = list(gres.data or [])
                if len(items) > per_page:
                    any_has_more = True
                    items = items[:per_page]
                for p in items:
                    by_id[int(p.id)] = p

        projects = list(by_id.values())
        projects.sort(key=lambda p: (p.path_with_namespace or "").lower())
        start = (page_n - 1) * per_page if search else 0
        end = start + per_page
        if len(projects) > end:
            any_has_more = True
        projects = projects[start:end]
        opts = [
            FilterOption(id=str(p.path_with_namespace), label=str(getattr(p, "name_with_namespace", None) or p.path_with_namespace))
            for p in projects
        ]
        return FilterOptionsResponse(success=True, options=opts, page=page_n, limit=limit, has_more=any_has_more)

    async def _unscoped_project_filter_options(
        self, search: str | None, exclude_paths: list[str], per_page: int, page_n: int, limit: int, page: int,
    ) -> FilterOptionsResponse:
        c = self.c
        server_search = search
        if exclude_paths and not search:
            fetch_per_page = min(_MAX_PER_PAGE, per_page * 2 + 1)
        else:
            fetch_per_page = per_page + 1

        if c._is_admin or c._is_auditor:
            proj_scope: dict[str, object] = {}
        else:
            proj_scope = {"min_access_level": 10}

        proj_kwargs: dict[str, object] = {
            "search": server_search, "get_all": False, "simple": True, **proj_scope,
        }
        if server_search:
            proj_kwargs["search_namespaces"] = True
        if not server_search:
            proj_kwargs["order_by"] = "path"
            proj_kwargs["sort"] = "asc"

        from app.connectors.sources.gitlab.projects import _namespace_full_path, _namespace_under_any_prefix

        if search:
            needle = search.casefold()
            projects, has_more, error = await self._scan_filter_option_pages(
                c.data_source.list_projects,
                list_kwargs=proj_kwargs,
                matcher=lambda p: _local_match_project(p, needle) and not _namespace_under_any_prefix(_namespace_full_path(p), exclude_paths),
                page=page_n, per_page=per_page,
                progress_label="GitLab project filter search",
            )
            if error:
                self.logger.warning("GitLab list_projects failed for filter options (search=%r, page=%s): %s", search, page, error)
                return FilterOptionsResponse(success=False, options=[], page=page, limit=limit, has_more=False, message=error)
            if c._is_auditor and not projects:
                c.scope.warn_auditor_fallback_once("projects")
                fb_kwargs = c.scope.picker_kwargs_for_auditor_projects_fallback(proj_kwargs)
                projects, has_more, error = await self._scan_filter_option_pages(
                    c.data_source.list_projects,
                    list_kwargs=fb_kwargs,
                    matcher=lambda p: _local_match_project(p, needle) and not _namespace_under_any_prefix(_namespace_full_path(p), exclude_paths),
                    page=page_n, per_page=per_page,
                    progress_label="GitLab project filter search [auditor fallback]",
                )
                if error:
                    self.logger.warning("GitLab list_projects auditor fallback failed (search=%r, page=%s): %s", search, page, error)
        else:
            proj_kwargs["page"] = page_n
            proj_kwargs["per_page"] = fetch_per_page
            res = await c.runtime.ds_call(c.data_source.list_projects, **proj_kwargs)
            if not res.success:
                self.logger.warning("GitLab list_projects failed for filter options (search=%r, page=%s): %s", search, page, res.error)
                return FilterOptionsResponse(success=False, options=[], page=page, limit=limit, has_more=False, message=res.error)
            projects = list(res.data or [])
            raw_count = len(projects)
            if c._is_auditor and raw_count == 0:
                c.scope.warn_auditor_fallback_once("projects")
                fb_kwargs = c.scope.picker_kwargs_for_auditor_projects_fallback(proj_kwargs)
                fb_res = await c.runtime.ds_call(c.data_source.list_projects, **fb_kwargs)
                if fb_res.success:
                    projects = list(fb_res.data or [])
                    raw_count = len(projects)
            if exclude_paths:
                projects = [
                    p for p in projects
                    if not _namespace_under_any_prefix(_namespace_full_path(p), exclude_paths)
                ]
            has_more = raw_count >= fetch_per_page
            if len(projects) > per_page:
                projects = projects[:per_page]

        opts = [
            FilterOption(id=str(p.path_with_namespace), label=str(getattr(p, "name_with_namespace", None) or p.path_with_namespace))
            for p in projects
        ]
        return FilterOptionsResponse(success=True, options=opts, page=page, limit=limit, has_more=has_more)

    # ------------------------------------------------------------------
    # Multi-page scanner
    # ------------------------------------------------------------------

    async def _scan_filter_option_pages(
        self,
        method: Callable,
        /,
        *args: object,
        list_kwargs: dict[str, object],
        matcher: Callable[[object], bool],
        page: int,
        per_page: int,
        progress_label: str,
    ) -> tuple[list[object], bool, str | None]:
        """Walk paged GitLab results locally until the requested match page is filled."""
        c = self.c
        target_count = (max(1, int(page)) * per_page) + 1
        matched: list[object] = []
        upstream_page = 1
        while True:
            kwargs = dict(list_kwargs)
            kwargs["page"] = upstream_page
            kwargs["per_page"] = _MAX_PER_PAGE
            res = await c.runtime.ds_call(method, *args, **kwargs)
            if not res.success:
                return [], False, res.error
            items = list(res.data or [])
            matched.extend(item for item in items if matcher(item))
            if len(matched) >= target_count:
                break
            if len(items) < _MAX_PER_PAGE:
                break
            if upstream_page >= _MAX_SCAN_PAGES:
                self.logger.debug("%s: stopped after %s page(s), local matches=%s", progress_label, upstream_page, len(matched))
                break
            upstream_page += 1
        start = (max(1, int(page)) - 1) * per_page
        end = start + per_page
        return matched[start:end], len(matched) > end, None


# ------------------------------------------------------------------
# Module-level static helpers
# ------------------------------------------------------------------

def _clamp_per_page(limit: int) -> int:
    """Clamp UI-supplied limit into GitLab's per_page range."""
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 20
    if n <= 0:
        n = 20
    return min(n, _MAX_PER_PAGE - 1)


def _is_short_search(search: str | None) -> bool:
    """True when search is non-empty but below GitLab's partial-match threshold."""
    if search is None:
        return False
    return 0 < len(search) < GITLAB_SEARCH_MIN_PARTIAL_CHARS


def _short_search_filter_options_response(page: int, limit: int) -> FilterOptionsResponse:
    return FilterOptionsResponse(
        success=True, options=[], page=page, limit=limit, has_more=False,
        message=f"Type at least {GITLAB_SEARCH_MIN_PARTIAL_CHARS} characters to search",
    )


def _local_match_group(g: object, needle: str) -> bool:
    """Case-insensitive substring match on a Group's name and full_path."""
    name = (getattr(g, "name", None) or "")
    path = (getattr(g, "full_path", None) or "")
    return needle in name.casefold() or needle in path.casefold()


def _local_match_project(p: object, needle: str) -> bool:
    """Case-insensitive substring match on a Project's name and path_with_namespace."""
    path = (getattr(p, "path_with_namespace", None) or "")
    name = (getattr(p, "name_with_namespace", None) or getattr(p, "name", None) or "")
    return needle in name.casefold() or needle in path.casefold()
