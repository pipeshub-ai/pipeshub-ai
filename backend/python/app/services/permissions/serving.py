"""`ServingPlanner`: whether a search is read with the viewer's scope filter, and for which connectors.

The filter is attached beside the request's own container arms, never in place of them (`ScopeFilter.defer`): a point that a trusted stamp
of a *served* connector decides is admitted or hidden by its stamp, so a hidden point no longer crowds the top-k; every other point (a
connector that is not served, a point never stamped or stamped by another version or generation) keeps the arms. The post-check stays on
every hit, so a plan can only narrow what the legacy request returns, and a wrong stamp costs recall, not access.

A connector is served only while its app document says so *now* (`currency.app_is_served`): the document is read for each search and
nothing is cached. Any doubt is the legacy request: no scopes in the cache, no connector served, a list too long for a query, a read
that fails or is slow.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import TYPE_CHECKING

from app.services.vector_db.filters import ScopeFilter, ScopeFilterTooLarge

from .errors import ViewerScopesUnavailable
from .metrics import SCOPES_UNAVAILABLE, SERVING_PLAN
from .scope_fields import SCOPE_FIELDS_VERSION, current_scope_generation

if TYPE_CHECKING:
    from .currency import StampCurrency
    from .scopes import ViewerScopes
    from .service import ViewerScopeService

_logger = logging.getLogger(__name__)
#: The plan's own reads (the connectors' documents) past this are not worth waiting for: the request is read with the legacy filter.
PLAN_BUDGET_SECONDS = 0.25
#: A viewer whose lists hold more ids than this (readable groups + readable scopes + restricted scopes they cannot read: the ids `plan` counts) is read
#: with the legacy request. Each candidate row is tested against these lists with array overlaps, so the cost grows with the count. Dense leg, p50 in
#: ms, legacy vs serving, PostgreSQL 17, 4 dimensions, limit 40, tables analysed: 20,000 records in 2,000 groups: 100 ids 7.1 vs 11.0, 300 ids 9.9 vs
#: 28.7, 600 ids 13.2 vs 68.8, 900 ids 13.7 vs 133.6; 200,000 records in 20,000 groups: 100 ids 27 vs 14, 300 ids 31 vs 32, 400 ids 32 vs 45, 600 ids
#: 34 vs 85. The cap is the count at which serving stays within about 3x of the legacy request on the small table and level on the large one.
# shortcut: array overlap against the viewer's lists, fine to a few hundred ids; a probe against a hashed set per viewer removes the cap.
MAX_SERVED_TERMS = 300


def stamp_filter(scopes: ViewerScopes, served: frozenset[str], *, defer: bool) -> ScopeFilter:
    """The filter for ``scopes`` over the ``served`` connectors: the deferring one a search carries, or (``defer=False``) the pure one the shadow
    comparison runs, which admits only what a stamp admits. The pending roots and blocked connectors the cached scopes carry are
    dropped: they are as old as the cache entry, which a registration or a rewrite does not invalidate, and a connector with a pending root is not in
    ``served`` (its document says so now). The state that decides is the connector's document, read for this search."""
    fresh = dataclasses.replace(scopes, pending=frozenset(), blocked=frozenset())
    return ScopeFilter.of(fresh, served, version=SCOPE_FIELDS_VERSION, generation=current_scope_generation(), defer=defer)


class ServingPlanner:
    def __init__(self, scopes: ViewerScopeService, currency: StampCurrency, budget_seconds: float = PLAN_BUDGET_SECONDS) -> None:
        self._scopes, self._currency, self._budget = scopes, currency, budget_seconds

    async def plan(self, org_id: str, user_key: str) -> ScopeFilter | None:
        """The filter to search with, or None for the legacy request. Never raises. Runs inside the organisation."""
        return (await self.plan_exact(org_id, user_key))[0]

    async def plan_exact(self, org_id: str, user_key: str) -> tuple[ScopeFilter | None, bool]:
        """`plan`, and whether every connector the viewer is gated into is served and exact and the viewer has no legacy app: then no point the request returns is left to the arms'
        guess, so it needs no over-fetch for the post-check's denials (the post-check itself stays on every hit)."""
        try:
            flt, exact, reason = await asyncio.wait_for(self._plan(org_id, user_key), self._budget)
        except TimeoutError:
            flt, exact, reason = None, False, "timeout"
        except Exception as exc:
            _logger.warning("serving plan failed: %s", type(exc).__name__)
            flt, exact, reason = None, False, "error"
        SERVING_PLAN.inc(reason)
        return flt, exact

    async def _plan(self, org_id: str, user_key: str) -> tuple[ScopeFilter | None, bool, str]:
        if not user_key:
            return None, False, "no_user"
        try:
            scopes = await self._scopes.scopes(org_id, user_key)      # the cache or ViewerScopesUnavailable, never a wait on the graph
        except ViewerScopesUnavailable as exc:
            SCOPES_UNAVAILABLE.inc(exc.reason.split(":", 1)[0])
            return None, False, "miss"
        if len(scopes.readable_groups) + len(scopes.readable_scopes) + len(scopes.unreadable_restricted) > MAX_SERVED_TERMS:
            return None, False, "broad"
        served, exact = await self._currency.read(scopes.gated_apps)
        if not served:
            return None, False, "no_served"
        flt = stamp_filter(scopes, served, defer=True)
        try:
            flt.lists()
        except ScopeFilterTooLarge:
            return None, False, "too_large"
        return flt, exact == scopes.gated_apps and not scopes.legacy_apps, "scopes"      # a legacy app is outside `gated_apps` and is read by the arms: not exact
