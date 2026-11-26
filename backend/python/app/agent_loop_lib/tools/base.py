"""Legacy import path; this module lives in ``harness_core.tools.base``."""

import sys

from harness_core.tools import base as _moved
from harness_core.tools.base import *  # noqa: F403

sys.modules[__name__] = _moved
