"""Legacy import path; this module lives in ``harness_core.kernel.context.base``."""

import sys

from harness_core.kernel.context import base as _moved
from harness_core.kernel.context.base import *  # noqa: F403

sys.modules[__name__] = _moved
