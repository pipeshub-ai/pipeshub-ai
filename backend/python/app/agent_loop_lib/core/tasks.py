"""Legacy import path; this module lives in ``harness_core.kernel.core.tasks``."""

import sys

from harness_core.kernel.core import tasks as _moved
from harness_core.kernel.core.tasks import *  # noqa: F403

sys.modules[__name__] = _moved
