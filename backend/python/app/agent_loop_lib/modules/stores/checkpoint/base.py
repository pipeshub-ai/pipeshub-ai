"""Legacy import path; this module lives in ``harness_core.kernel.ports.checkpoint``."""

import sys

from harness_core.kernel.ports import checkpoint as _moved
from harness_core.kernel.ports.checkpoint import *  # noqa: F403

sys.modules[__name__] = _moved
