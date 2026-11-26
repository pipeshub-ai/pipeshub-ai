"""Legacy import path; this module lives in ``harness_core.tools.executor``."""

import sys

from harness_core.tools import executor as _moved
from harness_core.tools.executor import *  # noqa: F403

sys.modules[__name__] = _moved
