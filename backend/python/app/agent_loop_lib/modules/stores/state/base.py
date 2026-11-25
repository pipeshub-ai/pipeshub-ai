"""Legacy import path; this module lives in ``harness_core.kernel.ports.state``."""

import sys

from harness_core.kernel.ports import state as _moved
from harness_core.kernel.ports.state import *  # noqa: F403

sys.modules[__name__] = _moved
