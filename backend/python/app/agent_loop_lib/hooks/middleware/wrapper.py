"""Legacy import path; this module lives in ``harness_core.kernel.hooks.middleware.wrapper``."""

import sys

from harness_core.kernel.hooks.middleware import wrapper as _moved
from harness_core.kernel.hooks.middleware.wrapper import *  # noqa: F403

sys.modules[__name__] = _moved
