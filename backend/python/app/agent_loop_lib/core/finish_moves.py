"""Legacy import path; this module lives in ``harness_core.kernel.core.finish_moves``."""

import sys

from harness_core.kernel.core import finish_moves as _moved
from harness_core.kernel.core.finish_moves import *  # noqa: F403

sys.modules[__name__] = _moved
