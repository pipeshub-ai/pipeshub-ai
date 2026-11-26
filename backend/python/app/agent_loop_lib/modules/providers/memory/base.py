"""Legacy import path; this module lives in ``harness_core.kernel.ports.memory``."""

import sys

from harness_core.kernel.ports import memory as _moved
from harness_core.kernel.ports.memory import *  # noqa: F403

sys.modules[__name__] = _moved
