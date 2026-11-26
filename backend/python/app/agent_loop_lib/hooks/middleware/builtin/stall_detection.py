"""Legacy import path; this module lives in ``harness_core.kernel.hooks.builtin.stall_detection``."""

import sys

from harness_core.kernel.hooks.builtin import stall_detection as _moved
from harness_core.kernel.hooks.builtin.stall_detection import *  # noqa: F403

sys.modules[__name__] = _moved
