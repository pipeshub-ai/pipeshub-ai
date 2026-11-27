"""Legacy import path; this module lives in ``harness_core.tools.tags``."""

import sys

from harness_core.tools import tags as _moved
from harness_core.tools.tags import *  # noqa: F403

sys.modules[__name__] = _moved
