"""Legacy import path; this module lives in ``harness_core.kernel.core.structured_output``."""

import sys

from harness_core.kernel.core import structured_output as _moved
from harness_core.kernel.core.structured_output import *  # noqa: F403

sys.modules[__name__] = _moved
