"""Legacy import path; this module lives in ``harness_core.kernel.core.context``."""

import sys

from harness_core.kernel.core import context as _moved
from harness_core.kernel.core.context import *  # noqa: F403

sys.modules[__name__] = _moved
