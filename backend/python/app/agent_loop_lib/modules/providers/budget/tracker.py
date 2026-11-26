"""Legacy import path; this module lives in ``harness_core.kernel.budget.tracker``."""

import sys

from harness_core.kernel.budget import tracker as _moved
from harness_core.kernel.budget.tracker import *  # noqa: F403

sys.modules[__name__] = _moved
