"""Legacy import path; this module lives in ``harness_core.kernel.events``."""

import sys

from harness_core.kernel import events as _moved
from harness_core.kernel.events import *  # noqa: F403

sys.modules[__name__] = _moved
