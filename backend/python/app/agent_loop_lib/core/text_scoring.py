"""Legacy import path; this module lives in ``harness_core.kernel.core.text_scoring``."""

import sys

from harness_core.kernel.core import text_scoring as _moved
from harness_core.kernel.core.text_scoring import *  # noqa: F403

sys.modules[__name__] = _moved
