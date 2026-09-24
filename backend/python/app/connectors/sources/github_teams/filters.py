"""
Dynamic filter-option pickers for the GitHub Teams connector.

Responsibilities:
- ``get_filter_options``: public entry point for the org/repo picker fields.
- ``_org_filter_options``: org picker (``list_user_orgs`` + local search).
- ``_repo_filter_options``: repo picker (``search_repositories`` when searching,
  ``list_org_repos``/``list_user_repos`` otherwise).

Pagination contracts (each mode has a different upstream shape):
- Browse, single source: GitHub pages directly, ``sort=full_name`` for a
  stable order. Exactly ``per_page`` rows are requested — an over-fetch of
  +1 would shift GitHub's page offset and silently skip one row per page.
- Browse, several orgs: GitHub cannot page a merged listing, so each org is
  scanned (in ``full_name`` order) deep enough to cover the requested window
  on its own, then merged/sorted/sliced locally. Scanning each org to the
  *global* target instead would let later-discovered rows sort into earlier
  positions and shift page boundaries between scrolls.
- Search: a scoped pass (org:/user: qualifiers) ranks the admin's own repos
  first, then public GitHub fills the page. The two passes cannot share a
  page number, so continuation state (prefix consumed, public page+offset)
  travels in an opaque cursor — the UI sends it back on scroll.
"""

from __future__ import annotations

import base64
import json
import time
from typing import TYPE_CHECKING, Any

from app.connectors.core.registry.filters import FilterOption, FilterOptionsResponse, SyncFilterKey

from .constants import (
    _FILTER_OPTIONS_MAX_PER_PAGE as _MAX_PER_PAGE,
    _FILTER_OPTIONS_MAX_SCAN_PAGES as _MAX_SCAN_PAGES,
    _ORG_SCOPE_CACHE_TTL_SECONDS,
    _SEARCH_PUBLIC_PAGES_PER_REQUEST,
    SEARCH_RESULTS_HARD_CAP,
)

if TYPE_CHECKING:
    from app.connectors.sources.github_teams.connector import GitHubTeamsConnector

# Cursor for the two-phase search stream: ``px`` = prefix (scoped) rows already
# emitted, ``pp`` = next public page (1-based), ``off`` = raw offset within it.
_CURSOR_KEYS = ("px", "pp", "off")


def _clamp_per_page(limit: int) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 20
    if n <= 0:
        n = 20
    return min(n, _MAX_PER_PAGE)


def _encode_cursor(state: dict[str, int]) -> str:
    return base64.urlsafe_b64encode(json.dumps(state, sort_keys=True).encode()).decode()


def _decode_cursor(cursor: str | None) -> dict[str, int] | None:
    if not cursor:
        return None
    try:
        state = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return {k: int(state[k]) for k in _CURSOR_KEYS}
    except Exception:
        return None


class FiltersHelper:
    """Dynamic org/repo filter-option provider for ``GitHubTeamsConnector``."""

    def __init__(self, connector: "GitHubTeamsConnector") -> None:
        self.c = connector
        self.logger = connector.logger
        self._org_scope_cache: tuple[float, list[str]] | None = None

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
                return await self._repo_filter_options(page, limit, search, cursor)
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

        The resolved org list is cached briefly — the picker fires a request
        per keystroke and the token's org membership does not change at that
        cadence.
        """
        c = self.c
        include = self._request_context(c, "_request_filter_context_group_paths")
        if include:
            return include, True

        cached = self._org_scope_cache
        if cached is not None and time.monotonic() - cached[0] < _ORG_SCOPE_CACHE_TTL_SECONDS:
            orgs = list(cached[1])
        else:
            orgs, ok = await c.users._resolve_target_orgs()
            if not ok:
                return [], False
            self._org_scope_cache = (time.monotonic(), list(orgs))

        exclude = {
            org.casefold()
            for org in self._request_context(c, "_request_filter_context_exclude_group_paths")
        }
        if exclude:
            orgs = [org for org in orgs if org.casefold() not in exclude]
        return orgs, True

    async def _repo_filter_options(
        self, page: int, limit: int, search: str | None, cursor: str | None,
    ) -> FilterOptionsResponse:
        c = self.c
        per_page = _clamp_per_page(limit)
        page_n = max(1, int(page))

        scope_orgs, ok = await self._scope_orgs()
        if not ok:
            return FilterOptionsResponse(
                success=False, options=[], page=page, limit=limit, has_more=False,
                message="Could not list GitHub organizations for this token.",
            )

        cursor_out: str | None = None
        if search:
            repos, has_more, cursor_out, error = await self._search_scoped_repos(
                scope_orgs, search, per_page, page_n, cursor,
            )
        else:
            repos, has_more, error = await self._list_scoped_repos(scope_orgs, per_page, page_n)
        if error is not None:
            return FilterOptionsResponse(
                success=False, options=[], page=page, limit=limit, has_more=False, message=error,
            )

        opts = [
            FilterOption(id=full_name, label=full_name)
            for r in repos
            if (full_name := str(getattr(r, "full_name", "") or ""))
        ]
        return FilterOptionsResponse(
            success=True, options=opts, page=page, limit=per_page,
            has_more=has_more, cursor=cursor_out,
        )

    # ------------------------------------------------------------------
    # Search mode: scoped prefix + public stream, cursor-paged
    # ------------------------------------------------------------------

    async def _search_scoped_repos(
        self, scope_orgs: list[str], search: str, per_page: int, page_n: int, cursor: str | None,
    ) -> tuple[list, bool, str | None, str | None]:
        """Search the in-scope orgs first, then the rest of public GitHub.

        Returns ``(rows, has_more, next_cursor, error)``.

        The scoped pass runs first so a user's own repositories always rank
        above same-named public ones; the public pass only runs when the
        scoped rows leave room on the page, which keeps the common case at a
        single Search API call — that pool is only 30 req/min.

        ``fork:true`` is essential: GitHub Search silently drops forks by
        default, and fork-heavy names (searching a well-known repo) would
        otherwise return a fraction of the real matches.

        The scoped result is a *prefix* of the stream, re-derived each request
        (one call, up to 100 rows) rather than carried in the cursor; the
        cursor tracks how much of the prefix and of the public stream has been
        emitted, so scrolling never duplicates or skips a row even though the
        two passes cannot share a page number.
        """
        c = self.c
        qualifiers = [f"org:{org}" for org in scope_orgs]
        login = getattr(c, "_github_login", None)
        if login:
            qualifiers.append(f"user:{login}")
        public_query = f"{search} in:name fork:true"
        scoped_query = f"{public_query} " + " ".join(qualifiers) if qualifiers else None

        state = _decode_cursor(cursor)
        skip = 0
        if state is None:
            state = {"px": 0, "pp": 1, "off": 0}
            # Page-based fallback for callers without the cursor: replay the
            # stream and drop the rows earlier pages showed.
            skip = (page_n - 1) * per_page

        prefix: list = []
        prefix_names: set[str] = set()
        scoped_error: str | None = None
        if scoped_query:
            res = await c.runtime.search_call(
                c.data_source.search_repositories, scoped_query, per_page=_MAX_PER_PAGE, page=1,
            )
            if res.success:
                prefix = list(res.data or [])
                prefix_names = {
                    fn for r in prefix
                    if (fn := str(getattr(r, "full_name", "") or "").casefold())
                }
            else:
                scoped_error = res.error
                self.logger.warning(
                    "Scoped repo search failed (search=%r): %s — falling back to public-only.",
                    search, res.error,
                )

        out: list = []

        def emit(row: Any) -> bool:
            nonlocal skip
            if skip > 0:
                skip -= 1
                return False
            out.append(row)
            return len(out) >= per_page

        page_full = False
        while state["px"] < len(prefix) and not page_full:
            page_full = emit(prefix[state["px"]])
            state["px"] += 1

        public_exhausted = False
        public_error: str | None = None
        max_public_page = SEARCH_RESULTS_HARD_CAP // _MAX_PER_PAGE
        fetches = 0
        while (
            not page_full
            and not public_exhausted
            and state["pp"] <= max_public_page
            and fetches < _SEARCH_PUBLIC_PAGES_PER_REQUEST
        ):
            res = await c.runtime.search_call(
                c.data_source.search_repositories, public_query,
                per_page=_MAX_PER_PAGE, page=state["pp"],
            )
            fetches += 1
            if not res.success:
                public_error = res.error
                self.logger.warning(
                    "Public repo search failed for filter options (search=%r): %s", search, res.error,
                )
                break
            rows = list(res.data or [])
            for i in range(state["off"], len(rows)):
                state["off"] = i + 1
                fn = str(getattr(rows[i], "full_name", "") or "").casefold()
                if not fn or fn in prefix_names:
                    continue
                if emit(rows[i]):
                    page_full = True
                    break
            if not page_full:
                if len(rows) < _MAX_PER_PAGE:
                    public_exhausted = True
                else:
                    state["pp"] += 1
                    state["off"] = 0

        if scoped_error and public_error and not out:
            return [], False, None, public_error or scoped_error

        prefix_remaining = state["px"] < len(prefix)
        public_remaining = (
            public_error is None
            and not public_exhausted
            and state["pp"] <= max_public_page
        )
        has_more = prefix_remaining or public_remaining
        return out, has_more, (_encode_cursor(state) if has_more else None), None

    # ------------------------------------------------------------------
    # Browse mode: GitHub-paged single source, locally-merged multi-org
    # ------------------------------------------------------------------

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
        # GitHub cannot do across orgs, so paging is local. Each org is
        # fetched in full_name order and scanned until IT ALONE could cover
        # the requested window (or it runs out): any unfetched repo then
        # sorts after everything fetched from its org, so the merged first
        # ``target_count`` rows are provably complete and page boundaries
        # stay stable across scrolls.
        target_count = page_n * per_page + 1
        by_full_name: dict[str, object] = {}
        any_success = False
        last_error: str | None = None

        for org in scope_orgs:
            org_count = 0
            for upstream_page in range(1, _MAX_SCAN_PAGES + 1):
                res = await c.runtime.ds_call(
                    c.data_source.list_org_repos, org, "all",
                    per_page=_MAX_PER_PAGE, page=upstream_page, sort="full_name",
                )
                if not res.success:
                    self.logger.warning("list_org_repos failed for filter options (org=%s): %s", org, res.error)
                    last_error = res.error
                    break
                any_success = True
                items = list(res.data or [])
                org_count += len(items)
                for r in items:
                    if name := str(getattr(r, "full_name", "") or ""):
                        by_full_name[name] = r
                if len(items) < _MAX_PER_PAGE or org_count >= target_count:
                    break
            else:
                # All _MAX_SCAN_PAGES fetched without exhausting the org or
                # covering the window: the browse list is silently shallower
                # than the org — say so once.
                self.logger.info(
                    "Repo picker: org %s truncated at %s repos (%s pages scanned) — "
                    "deeper rows are reachable via search.",
                    org, org_count, _MAX_SCAN_PAGES,
                )

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

        Requests exactly ``per_page`` rows: fetching ``per_page + 1`` would
        shift GitHub's page offset (it derives the offset from ``per_page``)
        and silently skip one row at every page boundary. ``has_more`` is
        therefore a full-page heuristic — when the total is an exact multiple
        the UI gets one empty final page, which it handles.
        """
        res = await self.c.runtime.ds_call(
            method, *args, per_page=per_page, page=page_n, sort="full_name",
        )
        if not res.success:
            self.logger.warning("%s failed for filter options: %s", label, res.error)
            return [], False, res.error
        raw = list(res.data or [])
        return raw[:per_page], len(raw) >= per_page, None
