"""Lanes a run takes only when its ``-m`` expression names their marker, so the default gate stays as fast as it was
(G-49); each is run by the workflow named here (tests/cohesion/test_g49_opt_in_lanes.py).

Kept out of ``tests/conftest.py``: importing a conftest by name runs it a second time as another module, and its
import-time ``NetworkGuard.install`` then stacks a second guard that no test ever admits."""

from __future__ import annotations

# Empty since R23-FU-GA26: the GA-26 journeys took under a second and, opt-in, went stale unseen after a main sync.
# Add a lane here only when it is too slow for the default gate and its workflow runs on every branch that changes it.
OPT_IN_LANES: dict[str, str] = {}
