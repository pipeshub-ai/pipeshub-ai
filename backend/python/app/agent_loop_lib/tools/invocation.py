"""Legacy import path; this module lives in ``harness_core.tools.invocation``."""

import sys

from harness_core.tools import invocation as _moved
from harness_core.tools.invocation import *  # noqa: F403

sys.modules[__name__] = _moved
