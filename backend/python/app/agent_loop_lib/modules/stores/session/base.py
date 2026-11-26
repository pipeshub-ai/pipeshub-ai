"""Legacy import path; this module lives in ``harness_core.kernel.ports.session``."""

import sys

from harness_core.kernel.ports import session as _moved
from harness_core.kernel.ports.session import *  # noqa: F403

sys.modules[__name__] = _moved
