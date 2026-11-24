"""The outage and poison-document scenario families at full size: all three seeds up to 200 jobs and the 1000-job cases.

The real pause verdict, fleet-breaker Lua and trial grant on Redis; the world is simulated (see
``tests/support/spend_sim.py``). With ``SPEND_IT_REDIS_URL`` it runs on a real Redis, otherwise on fakeredis (Lua).
``SPEND_SIM_FULL=1`` runs the 1000-job cases with every seed (about 25 minutes); by default they run with the first.
"""

import pytest

from tests.support.spend_scenarios import cases
from tests.support.spend_sim import run_world

pytestmark = pytest.mark.timeout(900)

CASES = cases("integration")


@pytest.mark.parametrize(("kwargs", "check"), [pytest.param(k, c, id=i) for i, k, c in CASES])
async def test_reviewer_scenarios_at_full_size(fleet, kwargs, check) -> None:
    check(await run_world(fleet, **kwargs))
