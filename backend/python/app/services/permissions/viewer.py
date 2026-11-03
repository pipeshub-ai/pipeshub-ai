"""`ViewerScopeResolver`: a pure function of the two ports.

It never decides readability itself. The graph check (`check_access`, behind `PrincipalView.readable`) stays
the one implementation of the access rule; the resolver only chooses which containers and scopes to ask about
and sorts the answers. Any port failure becomes `ViewerScopesUnavailable`.

The organisation's structure (record groups and scopes of an app) does not depend on the viewer, so it is kept
in memory per (org, app) for one ACL version: the first viewer after a change starts the read, concurrent viewers
share it and the rest find it done. It is used only when the caller passes a version it read from the counter, so
an unreadable counter means a fresh read.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, cast

from app.core.org_context import spawn_in_request

from .errors import ViewerScopesUnavailable
from .ports import AppFacts, ScopeNode
from .rules import uses_exclusion
from .scopes import UNVERSIONED, ViewerScopes

if TYPE_CHECKING:
    from .ports import HierarchyView, PrincipalView

Structure = tuple[frozenset[str], tuple[ScopeNode, ...], AppFacts]

#: A structure older than this is read again even if the version did not move: a lost or missing bump then costs at
#: most one cache TTL (`ViewerScopeCache.DEFAULT_TTL_SECONDS`), which is what the cache promises too.
MEMO_MAX_AGE_SECONDS = 300.0
MEMO_MAX_ENTRIES = 128
#: One read of an app's structure; a longer one is cancelled and the viewer takes the legacy path.
STRUCTURE_TIMEOUT_SECONDS = 30.0
#: Most ids (containers, scopes, exclusions) one viewer's answer may hold; more is "unavailable", never a wide scope.
DEFAULT_MAX_TERMS = 10_000


class ViewerScopeResolver:
    def __init__(
        self,
        hierarchy: HierarchyView,
        principals: PrincipalView,
        max_terms: int | None = DEFAULT_MAX_TERMS,
        memo_max_age_seconds: float = MEMO_MAX_AGE_SECONDS,
        memo_max_entries: int = MEMO_MAX_ENTRIES,
        structure_timeout_seconds: float = STRUCTURE_TIMEOUT_SECONDS,
    ) -> None:
        """`max_terms`: refuse (`ViewerScopesUnavailable("too_many_terms")`) rather than return more ids than a
        store can take in one filter; None removes the bound (tests only)."""
        self._structure_timeout = structure_timeout_seconds
        self._hierarchy = hierarchy
        self._principals = principals
        self._max_terms = max_terms
        self._max_age = memo_max_age_seconds
        self._max_entries = memo_max_entries
        # (org, app) -> (version, born, read); least recently used first.
        self._memo: OrderedDict[tuple[str, str], tuple[int, float, asyncio.Future[Structure]]] = OrderedDict()

    async def _fetch(self, org_id: str, app_id: str) -> Structure:
        try:
            groups, scopes, facts = await asyncio.wait_for(asyncio.gather(
                self._hierarchy.record_group_ids(org_id, app_id), self._hierarchy.scope_nodes(org_id, app_id),
                self._hierarchy.app_facts(org_id, app_id)), self._structure_timeout)
        except TimeoutError:
            raise ViewerScopesUnavailable("timeout") from None
        return groups, scopes, facts

    async def _structure(self, org_id: str, app_id: str, version: int) -> Structure:
        if version < 0:
            return await self._fetch(org_id, app_id)
        key = (org_id, app_id)
        now = time.monotonic()
        held = self._memo.get(key)
        if held is None or held[0] != version or now - held[1] > self._max_age:
            read = cast(
                "asyncio.Future[Structure]", spawn_in_request(self._fetch(org_id, app_id), name=f"viewer-structure:{app_id}")
            )
            read.add_done_callback(lambda f, k=key: self._forget_failed(k, f))
            held = (version, now, read)
            self._memo[key] = held
        self._memo.move_to_end(key)
        while len(self._memo) > self._max_entries:
            self._memo.popitem(last=False)
        # Shielded: a viewer that gives up (a deadline) leaves the read running, so the next one finds it done.
        return await asyncio.shield(held[2])

    def _forget_failed(self, key: tuple[str, str], read: asyncio.Future[Structure]) -> None:
        if read.cancelled() or read.exception() is not None:
            held = self._memo.get(key)
            if held is not None and held[2] is read:
                del self._memo[key]

    async def resolve(self, org_id: str, user_key: str, *, version: int = UNVERSIONED) -> ViewerScopes:
        if not org_id or not user_key:
            raise ViewerScopesUnavailable("no_viewer")
        try:
            who = await self._principals.principal(org_id, user_key)
            if not who.gated_app_ids:
                return ViewerScopes(org_id, user_key, principal_tokens=who.tokens, version=version)
            apps = sorted(who.gated_app_ids)
            parts = await asyncio.gather(*(self._structure(org_id, a, version) for a in apps))
            facts = dict(zip(apps, (f for _, _, f in parts), strict=True))
            # The path filter is not exact for an Intersection app that has record-group-level groups, and it
            # over-admits there: the app is left out of the answer (and not asked about) and named in `legacy_apps`.
            legacy = frozenset(a for a, f in facts.items() if f.record_group_level and uses_exclusion(f.connector_type))
            parts = [p for a, p in zip(apps, parts, strict=True) if a not in legacy]
            groups = frozenset[str]().union(*(g for g, _, _ in parts))
            scopes = tuple(s for _, ss, _ in parts for s in ss)
            pending = await self._hierarchy.pending_scopes(org_id)
            # A node that only a direct grant can open is not asked about unless the viewer holds one.
            asked = groups | {s.id for s in scopes if not s.needs_grant or s.id in who.granted_ids}
            readable = await self._principals.readable(org_id, who, asked) if asked else frozenset[str]()
        except ViewerScopesUnavailable:
            raise
        except Exception as exc:
            raise ViewerScopesUnavailable(f"port_failed:{type(exc).__name__}") from exc
        # Whatever a port returns beyond what was asked is dropped, so a port cannot widen the answer.
        readable &= asked
        out = ViewerScopes(
            org_id=org_id,
            user_key=user_key,
            gated_apps=who.gated_app_ids,
            open_apps=frozenset(a for a, f in facts.items() if f.opens and a not in legacy),
            readable_groups=groups & readable,
            readable_scopes=frozenset(s.id for s in scopes if s.id in readable),
            # The Intersection rule lists every restricted scope; the other rules only those restricted by their rule or
            # by deletion, never the (many) ones that merely do not inherit (see rules.py).
            unreadable_restricted=frozenset(
                s.id for s in scopes
                if s.restricted and s.id not in readable
                and (s.rule_restricted or uses_exclusion(facts[s.connector_id].connector_type))),
            principal_tokens=who.tokens,
            pending=pending,
            legacy_apps=legacy,
            version=version,
        )
        if self._max_terms is not None:
            terms = len(out.readable_containers) + len(out.readable_scopes) + len(out.unreadable_restricted)
            if terms > self._max_terms:
                raise ViewerScopesUnavailable(f"too_many_terms:{terms}")
        return out
