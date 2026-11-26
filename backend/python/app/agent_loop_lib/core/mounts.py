"""Legacy import path; this module lives in ``harness_core.kernel.core.mounts``."""

import sys

from harness_core.kernel.core import mounts as _moved
from harness_core.kernel.core.mounts import *  # noqa: F403

sys.modules[__name__] = _moved
