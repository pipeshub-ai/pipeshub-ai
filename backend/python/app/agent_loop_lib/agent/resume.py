"""Legacy import path; this module lives in ``harness_core.kernel.agent.resume``."""

import sys

from harness_core.kernel.agent import resume as _moved
from harness_core.kernel.agent.resume import *  # noqa: F403

sys.modules[__name__] = _moved
