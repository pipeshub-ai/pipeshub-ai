"""Legacy import path; this module lives in ``harness_core.kernel.planner``."""

import sys

from harness_core.kernel import planner as _moved
from harness_core.kernel.planner import *  # noqa: F403

sys.modules[__name__] = _moved
