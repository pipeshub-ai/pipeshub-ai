"""Legacy import path; this module lives in ``harness_core.kernel.agent.phase_driver``."""

import sys

from harness_core.kernel.agent import phase_driver as _moved
from harness_core.kernel.agent.phase_driver import *  # noqa: F403

sys.modules[__name__] = _moved
