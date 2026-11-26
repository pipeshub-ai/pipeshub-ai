"""Legacy import path; this module lives in ``harness_core.tools.special_route``."""

import sys

from harness_core.tools import special_route as _moved
from harness_core.tools.special_route import *  # noqa: F403

sys.modules[__name__] = _moved
