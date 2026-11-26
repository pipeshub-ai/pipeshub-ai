"""Legacy import path; this module lives in ``harness_core.tools.toolset``."""

import sys

from harness_core.tools import toolset as _moved
from harness_core.tools.toolset import *  # noqa: F403

sys.modules[__name__] = _moved
