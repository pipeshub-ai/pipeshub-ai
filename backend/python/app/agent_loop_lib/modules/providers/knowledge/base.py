"""Legacy import path; this module lives in ``harness_core.kernel.ports.knowledge``."""

import sys

from harness_core.kernel.ports import knowledge as _moved
from harness_core.kernel.ports.knowledge import *  # noqa: F403

sys.modules[__name__] = _moved
