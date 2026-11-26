"""Legacy import path; this module lives in ``harness_core.models.transports.opik_tracing``."""

import sys

from harness_core.models.transports import opik_tracing as _moved
from harness_core.models.transports.opik_tracing import *  # noqa: F403

sys.modules[__name__] = _moved
