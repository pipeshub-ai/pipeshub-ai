"""Legacy import path; this module lives in ``harness_core.kernel.agent.feasibility``."""

import sys

from harness_core.kernel.agent import feasibility as _moved
from harness_core.kernel.agent.feasibility import *  # noqa: F403

sys.modules[__name__] = _moved
