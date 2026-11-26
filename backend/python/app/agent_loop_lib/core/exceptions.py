"""Legacy import path; this module lives in ``harness_core.kernel.core.exceptions``."""

import sys

from harness_core.kernel.core import exceptions as _moved
from harness_core.kernel.core.exceptions import *  # noqa: F403

sys.modules[__name__] = _moved
