"""Legacy import path; this module lives in ``harness_core.kernel.core.usage_source``."""

import sys

from harness_core.kernel.core import usage_source as _moved
from harness_core.kernel.core.usage_source import *  # noqa: F403

sys.modules[__name__] = _moved
