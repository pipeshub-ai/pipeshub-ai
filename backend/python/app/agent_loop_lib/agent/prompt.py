"""Legacy import path; this module lives in ``harness_core.kernel.agent.prompt``."""

import sys

from harness_core.kernel.agent import prompt as _moved
from harness_core.kernel.agent.prompt import *  # noqa: F403

sys.modules[__name__] = _moved
