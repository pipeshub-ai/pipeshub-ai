"""Legacy import path; this module lives in ``harness_core.kernel.agent.observability``."""

import sys

from harness_core.kernel.agent import observability as _moved
from harness_core.kernel.agent.observability import *  # noqa: F403

sys.modules[__name__] = _moved
