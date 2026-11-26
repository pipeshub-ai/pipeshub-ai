"""Legacy import path; this module lives in ``harness_core.kernel.context.protection``."""

import sys

from harness_core.kernel.context import protection as _moved
from harness_core.kernel.context.protection import *  # noqa: F403

sys.modules[__name__] = _moved
