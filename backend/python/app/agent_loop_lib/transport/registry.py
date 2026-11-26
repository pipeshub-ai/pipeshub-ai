"""Legacy import path; this module lives in ``harness_core.models.transports.registry``."""

import sys

from harness_core.models.transports import registry as _moved
from harness_core.models.transports.registry import *  # noqa: F403

sys.modules[__name__] = _moved
