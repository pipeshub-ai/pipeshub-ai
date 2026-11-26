"""Legacy import path; this module lives in ``harness_core.kernel.runtime``."""

import sys

from harness_core.kernel import runtime as _moved
from harness_core.kernel.runtime import *  # noqa: F403

sys.modules[__name__] = _moved
