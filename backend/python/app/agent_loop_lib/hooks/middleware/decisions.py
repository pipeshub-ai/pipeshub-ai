"""Legacy import path; this module lives in ``harness_core.kernel.hooks.middleware.decisions``."""

import sys

from harness_core.kernel.hooks.middleware import decisions as _moved
from harness_core.kernel.hooks.middleware.decisions import *  # noqa: F403

sys.modules[__name__] = _moved
