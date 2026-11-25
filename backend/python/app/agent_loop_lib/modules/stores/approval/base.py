"""Legacy import path; this module lives in ``harness_core.kernel.ports.approval``."""

import sys

from harness_core.kernel.ports import approval as _moved
from harness_core.kernel.ports.approval import *  # noqa: F403

sys.modules[__name__] = _moved
