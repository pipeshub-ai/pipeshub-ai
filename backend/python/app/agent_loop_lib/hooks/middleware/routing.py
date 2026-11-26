"""Legacy import path; this module lives in ``harness_core.kernel.hooks.middleware.routing``."""

import sys

from harness_core.kernel.hooks.middleware import routing as _moved
from harness_core.kernel.hooks.middleware.routing import *  # noqa: F403

sys.modules[__name__] = _moved
