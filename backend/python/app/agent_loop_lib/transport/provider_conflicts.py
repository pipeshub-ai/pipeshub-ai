"""Legacy import path; this module lives in ``harness_core.models.transports.provider_conflicts``."""

import sys

from harness_core.models.transports import provider_conflicts as _moved
from harness_core.models.transports.provider_conflicts import *  # noqa: F403

sys.modules[__name__] = _moved
