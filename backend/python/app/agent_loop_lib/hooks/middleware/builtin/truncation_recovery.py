"""Legacy import path; this module lives in ``harness_core.kernel.hooks.builtin.truncation_recovery``."""

import sys

from harness_core.kernel.hooks.builtin import truncation_recovery as _moved
from harness_core.kernel.hooks.builtin.truncation_recovery import *  # noqa: F403

sys.modules[__name__] = _moved
