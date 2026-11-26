"""Legacy import path; this module lives in ``harness_core.kernel.context.manager``."""

import sys

from harness_core.kernel.context import manager as _moved
from harness_core.kernel.context.manager import *  # noqa: F403

sys.modules[__name__] = _moved
