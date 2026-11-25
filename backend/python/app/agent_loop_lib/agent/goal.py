"""Legacy import path; this module lives in ``harness_core.kernel.agent.goal``."""

import sys

from harness_core.kernel.agent import goal as _moved
from harness_core.kernel.agent.goal import *  # noqa: F403

sys.modules[__name__] = _moved
