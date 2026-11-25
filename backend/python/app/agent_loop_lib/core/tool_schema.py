"""Legacy import path; this module lives in ``harness_core.kernel.core.tool_schema``."""

import sys

from harness_core.kernel.core import tool_schema as _moved
from harness_core.kernel.core.tool_schema import *  # noqa: F403

sys.modules[__name__] = _moved
