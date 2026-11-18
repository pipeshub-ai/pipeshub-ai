"""The outage and poison-document scenarios, run on the real pause verdict, the fleet breaker's Lua and
the real trial grant. The world (provider, clock, arrivals) is simulated; every decision is real.

The unit tier runs the families at up to 200 jobs with one seed; the 1000-job cases and the other seeds
are in ``tests/integration/spend/test_pause_budget_simulation.py``.
"""

import pytest

from tests.support.spend_scenarios import cases
from tests.support.spend_sim import run_world

pytestmark = pytest.mark.timeout(120)

CASES = cases("unit")


@pytest.mark.parametrize(("kwargs", "check"), [pytest.param(k, c, id=i) for i, k, c in CASES])
async def test_outage_scenarios(fleet, kwargs, check) -> None:
    result = await run_world(fleet, **kwargs)
    check(result)
