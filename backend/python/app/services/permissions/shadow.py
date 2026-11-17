"""`ShadowComparator`: the scope filter, run beside the real search, never in its place.

The real search and its post-check answer as before. For a sampled fraction of searches, once per search, when its attempts are done and
it has settled on the attempt it keeps (the caller formats the response while the comparison runs), in a detached task (a search never
waits for it), the comparator runs the same queries again with the viewer's scope filter, and asks the post-check about
every record the filtered search returned that the real search did not return. Then it counts, by record:

* ``agree``: the filtered search returned it and the post-check admits it;
* ``over_admit``: the filtered search returned it and the post-check refuses it. The filter must only narrow, so each one is an alert
  (a bug in a stamp, or a stamp the structure has outrun: a container-stamped record under a scope that is being re-stamped is still admitted
  through its group until the stamp is rewritten, and the post-check is what catches it);
* ``recall_loss``: the post-check admits it and the filtered search did not return it (the real path found it; the filter hides it). Expected for a connector that is not served (`currency.app_is_served`) or a group without a usable stamp, and for a known narrowing of the filter: a union is narrower than "any path scope readable", because the excluded scopes include the unreadable-restricted and the pending ones.

Nothing the comparator computes is ever returned to a user. Cost is bounded: ``ACL_SHADOW_SAMPLE`` (default 0.1, validated at start-up by `wiring.shadow_settings_from_env`) of searches, at most
``max_inflight`` comparisons at a time (the rest are counted as skipped), a timeout on each step, and no comparison at all unless
``ACL_FILTER_MODE=shadow``. Metrics carry counts only. Logs carry the organisation, the viewer and counts: no record id or content (a filter
that over-admitted could have returned a record of another organisation, whose id must not be written down).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import TYPE_CHECKING, Any

from app.core.org_context import for_org, spawn_detached
from app.services.vector_db.filters import ScopeFilter, ScopeFilterTooLarge

from .currency import StampCurrency
from .metrics import SHADOW_COMPARED, SHADOW_SECONDS, SHADOW_SKIPPED
from .mode import AclFilterMode, scopes_or_legacy
from .serving import stamp_filter

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

    from .service import ViewerScopeService

_logger = logging.getLogger(__name__)


class ShadowIncomplete(Exception):
    """The shadow search could not search every collection: what it returned is not comparable (a missing record would read as recall loss)."""


class ShadowComparator:
    def __init__(
        self,
        scopes: ViewerScopeService,
        graph_provider: IGraphDBProvider,
        *,
        sample_rate: float = 0.1,
        max_inflight: int = 4,
        step_timeout_seconds: float = 5.0,
        max_apps: int = 500,
        log_per_minute: int = 30,
        rng: random.Random | None = None,
    ) -> None:
        self._scopes, self._graph, self._currency = scopes, graph_provider, StampCurrency(graph_provider)
        self._rate = sample_rate
        self._max_inflight, self._timeout, self._max_apps = max_inflight, step_timeout_seconds, max_apps
        self._log_per_minute = log_per_minute
        self._rng = rng or random.Random()
        self._inflight: set[asyncio.Task[None]] = set()
        self._window_start, self._logged = 0.0, 0

    def submit(
        self, org_id: str, user_key: str, returned: Collection[str], admitted: Collection[str],
        run_scoped: Callable[[ScopeFilter], Awaitable[list[Any]]], check: Callable[[set[str]], Awaitable[Collection[str]]],
    ) -> None:
        """Compare in the background. ``returned``: the virtual records the real search found; ``admitted``: those the post-check allowed;
        ``run_scoped(filter)``: the same search with the filter, as result dicts; ``check(vrids)``: the post-check, the admitted ones.
        Never raises and never waits."""
        if not returned:
            return
        if not user_key:
            SHADOW_SKIPPED.inc("no_user")           # the viewer "" is nobody: resolving it would compare against a made-up user
            return
        if self._rate < 1.0 and self._rng.random() >= self._rate:
            SHADOW_SKIPPED.inc("sampled_out")
            return
        if len(self._inflight) >= self._max_inflight:
            SHADOW_SKIPPED.inc("busy")
            return
        try:
            task = spawn_detached(self.compare(org_id, user_key, set(returned), set(admitted), run_scoped, check), name="acl-shadow")
        except RuntimeError:  # no running loop
            return
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

    async def wait(self) -> None:
        """Wait for the comparisons running now (tests, shutdown)."""
        if self._inflight:
            await asyncio.gather(*self._inflight, return_exceptions=True)

    async def compare(
        self, org_id: str, user_key: str, returned: set[str], admitted: set[str],
        run_scoped: Callable[[ScopeFilter], Awaitable[list[Any]]], check: Callable[[set[str]], Awaitable[Collection[str]]],
    ) -> dict[str, int]:
        """The counts by outcome (empty when skipped)."""
        t0 = time.perf_counter()
        result = "skipped"
        try:
            with for_org(org_id, job="acl-shadow"):
                scopes = await scopes_or_legacy(AclFilterMode.SHADOW, self._scopes, org_id, user_key)
                if scopes is None:
                    SHADOW_SKIPPED.inc("scopes_unavailable")
                    return {}
                if len(scopes.gated_apps) > self._max_apps:      # one batched read, but a filter over this many connectors is not what serving sends
                    SHADOW_SKIPPED.inc("no_current_connector")
                    return {}
                served = await asyncio.wait_for(self._currency.served(scopes.gated_apps), self._timeout)
                if not served:
                    SHADOW_SKIPPED.inc("no_current_connector")
                    return {}
                flt = stamp_filter(scopes, served, defer=False)      # only what the stamps of the served connectors admit; the rest is left out, as recall loss
                found = await asyncio.wait_for(run_scoped(flt), self._timeout)
                scoped = {str(vrid) for r in found if isinstance(r, dict) and (vrid := (r.get("metadata") or {}).get("virtualRecordId")) is not None}
                unjudged = scoped - returned            # never post-checked by the real search: ask it now
                ok = scoped & admitted
                if unjudged:
                    ok |= unjudged & set(await asyncio.wait_for(check(unjudged), self._timeout))
            result = "done"
        except asyncio.CancelledError:
            SHADOW_SKIPPED.inc("cancelled")
            raise
        except NotImplementedError:
            SHADOW_SKIPPED.inc("unsupported")
            return {}
        except ScopeFilterTooLarge:
            SHADOW_SKIPPED.inc("too_large")
            return {}
        except TimeoutError:
            SHADOW_SKIPPED.inc("timeout")
            return {}
        except Exception as exc:
            SHADOW_SKIPPED.inc("error")
            _logger.warning("acl shadow comparison failed: %s", type(exc).__name__)
            return {}
        finally:
            SHADOW_SECONDS.observe(result, value=time.perf_counter() - t0)
        counts = {"agree": len(ok), "over_admit": len(scoped - ok), "recall_loss": len(admitted & returned - scoped)}
        for outcome, n in counts.items():
            if n:
                SHADOW_COMPARED.inc(outcome, value=n)
        if counts["over_admit"] and self._may_log():
            _logger.error("acl_shadow over_admit org=%s user=%s count=%d agree=%d", org_id, user_key, counts["over_admit"], counts["agree"])
        return counts

    def _may_log(self) -> bool:
        now = time.monotonic()
        if now - self._window_start >= 60:
            self._window_start, self._logged = now, 0
        self._logged += 1
        return self._logged <= self._log_per_minute
