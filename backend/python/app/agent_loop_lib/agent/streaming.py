"""Legacy import path; this module lives in ``harness_core.kernel.agent.streaming``."""

import sys

from harness_core.kernel.agent import streaming as _moved
from harness_core.kernel.agent.streaming import *  # noqa: F403

sys.modules[__name__] = _moved
