"""Legacy import path; this module lives in ``harness_core.kernel.hooks.builtin.turn_guards``."""

import sys

from harness_core.kernel.hooks.builtin import turn_guards as _moved
from harness_core.kernel.hooks.builtin.turn_guards import *  # noqa: F403

sys.modules[__name__] = _moved
