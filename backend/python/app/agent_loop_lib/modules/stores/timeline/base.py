"""Legacy import path; this module lives in ``harness_core.kernel.ports.timeline``."""

import sys

from harness_core.kernel.ports import timeline as _moved
from harness_core.kernel.ports.timeline import *  # noqa: F403

sys.modules[__name__] = _moved
