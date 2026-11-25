"""Legacy import path; this module lives in ``harness_core.tools.decorators``."""

import sys

from harness_core.tools import decorators as _moved
from harness_core.tools.decorators import *  # noqa: F403

sys.modules[__name__] = _moved
