"""Legacy import path; this module lives in ``harness_core.kernel.core.interfaces``."""

import sys

from harness_core.kernel.core import interfaces as _moved
from harness_core.kernel.core.interfaces import *  # noqa: F403

sys.modules[__name__] = _moved
