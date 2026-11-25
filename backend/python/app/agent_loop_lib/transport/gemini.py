"""Legacy import path; this module lives in ``harness_core.models.transports.gemini``."""

import sys

from harness_core.models.transports import gemini as _moved
from harness_core.models.transports.gemini import *  # noqa: F403

sys.modules[__name__] = _moved
