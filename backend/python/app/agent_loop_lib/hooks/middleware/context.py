"""Legacy import path; this module lives in ``harness_core.kernel.hooks.middleware.context``."""

import sys

from harness_core.kernel.hooks.middleware import context as _moved
from harness_core.kernel.hooks.middleware.context import *  # noqa: F403

sys.modules[__name__] = _moved
