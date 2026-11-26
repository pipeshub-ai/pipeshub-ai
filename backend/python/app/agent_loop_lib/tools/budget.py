"""Legacy import path; this module lives in ``harness_core.tools.budget``."""

import sys

from harness_core.tools import budget as _moved
from harness_core.tools.budget import *  # noqa: F403

sys.modules[__name__] = _moved
