"""Legacy import path; this module lives in ``harness_core.kernel.agent.hook_dispatch``."""

import sys

from harness_core.kernel.agent import hook_dispatch as _moved
from harness_core.kernel.agent.hook_dispatch import *  # noqa: F403

sys.modules[__name__] = _moved
