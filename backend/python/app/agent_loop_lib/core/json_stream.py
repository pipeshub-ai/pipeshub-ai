"""Legacy import path; this module lives in ``harness_core.kernel.core.json_stream``."""

import sys

from harness_core.kernel.core import json_stream as _moved
from harness_core.kernel.core.json_stream import *  # noqa: F403

sys.modules[__name__] = _moved
