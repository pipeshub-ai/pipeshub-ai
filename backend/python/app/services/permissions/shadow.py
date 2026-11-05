"""`ShadowComparator`: the scope filter, run beside the real search, never in its place.

The real search and its post-check answer as before. For a sampled fraction of searches, after the answer is built and in a detached task
(a search never waits for it), the comparator runs the same queries again with the viewer's scope filter, and asks the post-check about
every record the filtered search returned that the real search did not return. Then it counts, by record:

* ``agree``: the filtered search returned it and the post-check admits it;
* ``over_admit``: the filtered search returned it and the post-check refuses it. The filter must only narrow, so each one is an alert
  (a bug in a stamp, or a stamp the structure has outrun: a container-stamped record under a scope that is being re-stamped is still admitted
  through its group until the stamp is rewritten, and the post-check is what catches it);
* ``recall_loss``: the post-check admits it and the filtered search did not return it (the real path found it; the filter hides it). Expected for a group without a usable stamp, and for a known narrowing of the filter: a union is narrower than "any path scope readable", because the excluded scopes include the unreadable-restricted and the pending ones.

Nothing the comparator computes is ever returned to a user. Cost is bounded: ``ACL_SHADOW_SAMPLE`` (default 0.1) of searches, at most
``max_inflight`` comparisons at a time (the rest are counted as skipped), a timeout on each step, and no comparison at all unless
``ACL_FILTER_MODE=shadow``. Metrics carry counts only. Logs carry the organisation, the viewer and counts: no record id or content (a filter
that over-admitted could have returned a record of another organisation, whose id must not be written down).
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from typing import TYPE_CHECKING, Any

from app.core.org_context import for_org, spawn_detached
from app.services.vector_db.filters import ScopeFilter, ScopeFilterTooLarge

from .metrics import SHADOW_COMPARED, SHADOW_SECONDS, SHADOW_SKIPPED
from .mode import AclFilterMode, scopes_or_legacy
from .scope_fields import SCOPE_FIELDS_VERSION, current_scope_generation

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

    from .service import ViewerScopeService

ENV_SHADOW_SAMPLE = "ACL_SHADOW_SAMPLE"
_logger = logging.getLogger(__name__)
#: The scope back-fill checkpoint and the unsafe mark of an app document (see `vector_db.scope_backfill` and `structure_generation`).
_BACKFILL_STATE, _BACKFILL_VERSION, _BACKFILL_GENERATION, _UNSAFE = "scopeBackfillState", "scopeBackfillVersion", "scopeBackfillGeneration", "scopeProjectionUnsafe"
_DONE = "done"


def sample_rate_from_env() -> float:
    try:
        return min(1.0, max(0.0, float(os.environ.get(ENV_SHADOW_SAMPLE, "0.1"))))
    except ValueError:
        return 0.1


class ShadowComparator:
    def __init__(
        self,
        scopes: ViewerScopeService,
        graph_provider: IGraphDBProvider,
        *,
        sample_rate: float | None = None,
        max_inflight: int = 4,
        step_timeout_seconds: float = 5.0,
        max_apps: int = 500,
        log_per_minute: int = 30,
        rng: random.Random | None = None,
    ) -> None:
        self._scopes, self._graph = scopes, graph_provider
        self._rate = sample_rate_from_env() if sample_rate is None else sample_rate
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

    async def _current_connectors(self, apps: Collection[str]) -> set[str]:
        """The apps whose stamps may be used: the back-fill finished them at the current field version and scope generation, and
        nothing marked them unsafe. (A structural change after the pass is not tracked yet: the shadow exists to show what that costs.)"""
        generation = current_scope_generation()
        if generation < 0 or len(apps) > self._max_apps:
            return set()
        docs = await asyncio.gather(*(self._graph.get_document(a, "apps") for a in sorted(apps)), return_exceptions=True)
        return {a for a, d in zip(sorted(apps), docs, strict=True) if isinstance(d, dict) and d.get(_BACKFILL_STATE) == _DONE
                and d.get(_BACKFILL_VERSION) == SCOPE_FIELDS_VERSION and d.get(_BACKFILL_GENERATION) == generation and not d.get(_UNSAFE)}

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
                current = await asyncio.wait_for(self._current_connectors(scopes.gated_apps), self._timeout)
                if not current:
                    SHADOW_SKIPPED.inc("no_current_connector")
                    return {}
                flt = ScopeFilter.of(scopes, current, version=SCOPE_FIELDS_VERSION, generation=current_scope_generation())
                found = await asyncio.wait_for(run_scoped(flt), self._timeout)
                scoped = {str(vrid) for r in found if isinstance(r, dict) and (vrid := (r.get("metadata") or {}).get("virtualRecordId")) is not None}
                unjudged = scoped - returned            # never post-checked by the real search: ask it now
                ok = scoped & admitted
                if unjudged:
                    ok |= unjudged & set(await asyncio.wait_for(check(unjudged), self._timeout))
            result = "done"
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
