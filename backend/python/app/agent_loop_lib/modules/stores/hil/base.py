"""Legacy import path; this module lives in ``harness_core.kernel.ports.hil``."""

import sys

from harness_core.kernel.ports import hil as _moved
from harness_core.kernel.ports.hil import *  # noqa: F403

sys.modules[__name__] = _moved
