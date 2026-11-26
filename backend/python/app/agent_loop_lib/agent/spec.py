"""Legacy import path; this module lives in ``harness_core.kernel.agent.spec``."""

import sys

from harness_core.kernel.agent import spec as _moved
from harness_core.kernel.agent.spec import *  # noqa: F403

sys.modules[__name__] = _moved
