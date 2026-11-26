"""Legacy import path; this module lives in ``harness_core.kernel.core.messages``."""

import sys

from harness_core.kernel.core import messages as _moved
from harness_core.kernel.core.messages import *  # noqa: F403

sys.modules[__name__] = _moved
