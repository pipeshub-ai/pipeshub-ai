"""Legacy import path; this module lives in ``harness_core.kernel.hooks.events``."""

import sys

from harness_core.kernel.hooks import events as _moved
from harness_core.kernel.hooks.events import *  # noqa: F403

sys.modules[__name__] = _moved
