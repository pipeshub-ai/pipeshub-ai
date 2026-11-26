"""Legacy import path; this module lives in ``harness_core.kernel.agent.tool_loop``."""

import sys

from harness_core.kernel.agent import tool_loop as _moved
from harness_core.kernel.agent.tool_loop import *  # noqa: F403

sys.modules[__name__] = _moved
