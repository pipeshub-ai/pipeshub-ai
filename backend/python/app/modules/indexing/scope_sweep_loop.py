"""The scope sweep, run by the indexing service (`app.services.vector_db.scope_sweep`).

Off unless the scope fields are on (`ACL_SCOPE_FIELDS`, or a filter mode other than legacy): with them off nothing is started and nothing is read.
This is what recovers a write whose process was killed and finishes the pending roots without an operator.

Safe beside other replicas and other sweeps: a connector's rewrite and back-fill run under its lease (a second one finds it busy and moves on),
a recovery is a compare-and-set that a loser repeats next round, and a failing connector's retry time is kept on the connector, not in the
process. ``ACL_SCOPE_SWEEP_INTERVAL_SECONDS`` (default 300, at least 30) is the wait between rounds; each wait is jittered by up to a fifth so that
replicas started together do not sweep together.
"""

from __future__ import annotations

import asyncio
import os
import random
from typing import TYPE_CHECKING, Any

from app.modules.indexing.vector_membership_backfill import _resolve_indexing_pipeline
from app.services.permissions.mode import scope_fields_enabled
from app.services.vector_db.scope_sweep import ScopeSweep, sweep_forever

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

ENV_SWEEP_INTERVAL = "ACL_SCOPE_SWEEP_INTERVAL_SECONDS"
DEFAULT_INTERVAL_SECONDS = 300.0
MIN_INTERVAL_SECONDS = 30.0
STARTUP_DELAY_SECONDS = 60.0      # the stores and the scope generation are settled before the first round


def sweep_interval(env: dict[str, str] | None = None) -> float:
    """A value that is not a number, or is below the minimum, is the default (a typo must not make the sweep spin)."""
    raw = (env if env is not None else os.environ).get(ENV_SWEEP_INTERVAL, "").strip()
    try:
        value = float(raw) if raw else DEFAULT_INTERVAL_SECONDS
    except ValueError:
        return DEFAULT_INTERVAL_SECONDS
    return value if value >= MIN_INTERVAL_SECONDS else DEFAULT_INTERVAL_SECONDS


async def run_scope_sweep_loop(
    app_container: Any,  # noqa: ANN401
    graph_provider: IGraphDBProvider,
    all_org_ids: Callable[[IGraphDBProvider], Awaitable[list[str]]],
    *,
    sleep: Any = asyncio.sleep,  # noqa: ANN401
) -> None:
    if not scope_fields_enabled():
        return
    logger = app_container.logger()
    interval = sweep_interval()
    await sleep(STARTUP_DELAY_SECONDS * random.random())

    class Lazy:
        """The sweep, built at its first use: the indexing pipeline may not be up when the service starts, and is asked for again each round."""

        sweep: ScopeSweep | None = None

        async def run_once(self, org: str, connectors: Any) -> list:  # noqa: ANN401
            if self.sweep is None:
                pipeline = await _resolve_indexing_pipeline(app_container)
                if pipeline is None:
                    raise RuntimeError("the indexing pipeline is not available yet")
                self.sweep = ScopeSweep(graph_provider, pipeline.vector_db_service, pipeline.collection_locator, logger=logger)
            return await self.sweep.run_once(org, connectors)

    async def jittered(seconds: float) -> None:
        await sleep(seconds * (1 + 0.2 * random.random()))

    await sweep_forever(Lazy(), lambda: all_org_ids(graph_provider), interval, log=logger, sleep=jittered)     # type: ignore[arg-type]
