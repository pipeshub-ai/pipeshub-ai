"""Legacy import path; this module lives in ``harness_core.kernel.core.streaming``."""

import sys

from harness_core.kernel.core import streaming as _moved
from harness_core.kernel.core.streaming import *  # noqa: F403

sys.modules[__name__] = _moved
