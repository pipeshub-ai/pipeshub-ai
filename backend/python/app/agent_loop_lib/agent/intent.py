"""Legacy import path; this module lives in ``harness_core.kernel.agent.intent``."""

import sys

from harness_core.kernel.agent import intent as _moved
from harness_core.kernel.agent.intent import *  # noqa: F403

sys.modules[__name__] = _moved
