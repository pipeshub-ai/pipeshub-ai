"""Legacy import path; this module lives in ``harness_core.kernel.agent.single_shot_runner``."""

import sys

from harness_core.kernel.agent import single_shot_runner as _moved
from harness_core.kernel.agent.single_shot_runner import *  # noqa: F403

sys.modules[__name__] = _moved
