"""Legacy import path; this module lives in ``harness_core.kernel.budget.base``."""

import sys

from harness_core.kernel.budget import base as _moved
from harness_core.kernel.budget.base import *  # noqa: F403

sys.modules[__name__] = _moved
