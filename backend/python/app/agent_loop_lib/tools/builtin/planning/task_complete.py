"""Legacy import path; this module lives in ``harness_core.tools.task_complete``."""

import sys

from harness_core.tools import task_complete as _moved
from harness_core.tools.task_complete import *  # noqa: F403

sys.modules[__name__] = _moved
