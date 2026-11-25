"""Legacy import path; this module lives in ``harness_core.kernel.hooks.builtin.budget_guard``."""

import sys

from harness_core.kernel.hooks.builtin import budget_guard as _moved
from harness_core.kernel.hooks.builtin.budget_guard import *  # noqa: F403

sys.modules[__name__] = _moved
