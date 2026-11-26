"""Legacy import path; this module lives in ``harness_core.kernel.budget.pricing``."""

import sys

from harness_core.kernel.budget import pricing as _moved
from harness_core.kernel.budget.pricing import *  # noqa: F403

sys.modules[__name__] = _moved
