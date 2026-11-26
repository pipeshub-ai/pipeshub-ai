"""Legacy import path; this module lives in ``harness_core.kernel.context.errors``."""

import sys

from harness_core.kernel.context import errors as _moved
from harness_core.kernel.context.errors import *  # noqa: F403

sys.modules[__name__] = _moved
