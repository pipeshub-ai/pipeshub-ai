"""Legacy import path; this module lives in ``harness_core.tools.provider``."""

import sys

from harness_core.tools import provider as _moved
from harness_core.tools.provider import *  # noqa: F403

sys.modules[__name__] = _moved
