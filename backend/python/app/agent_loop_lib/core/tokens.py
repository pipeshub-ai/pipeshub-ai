"""Legacy import path; this module lives in ``harness_core.kernel.core.tokens``."""

import sys

from harness_core.kernel.core import tokens as _moved
from harness_core.kernel.core.tokens import *  # noqa: F403

sys.modules[__name__] = _moved
