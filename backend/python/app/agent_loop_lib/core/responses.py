"""Legacy import path; this module lives in ``harness_core.kernel.core.responses``."""

import sys

from harness_core.kernel.core import responses as _moved
from harness_core.kernel.core.responses import *  # noqa: F403

sys.modules[__name__] = _moved
