"""Legacy import path; this module lives in ``harness_core.kernel.agent.loops``."""

import sys

from harness_core.kernel.agent import loops as _moved
from harness_core.kernel.agent.loops import *  # noqa: F403

sys.modules[__name__] = _moved
