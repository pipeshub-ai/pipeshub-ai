"""Legacy import path; this module lives in ``harness_core.kernel.hooks.registry``."""

import sys

from harness_core.kernel.hooks import registry as _moved
from harness_core.kernel.hooks.registry import *  # noqa: F403

sys.modules[__name__] = _moved
