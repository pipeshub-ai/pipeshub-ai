"""Legacy import path; this module lives in ``harness_core.kernel.hooks.middleware.pipeline``."""

import sys

from harness_core.kernel.hooks.middleware import pipeline as _moved
from harness_core.kernel.hooks.middleware.pipeline import *  # noqa: F403

sys.modules[__name__] = _moved
