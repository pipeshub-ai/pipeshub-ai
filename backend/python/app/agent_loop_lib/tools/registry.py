"""Legacy import path; this module lives in ``harness_core.tools.registry``."""

import sys

from harness_core.tools import registry as _moved
from harness_core.tools.registry import *  # noqa: F403

sys.modules[__name__] = _moved
