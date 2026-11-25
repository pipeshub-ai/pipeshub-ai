"""Legacy import path; this module lives in ``harness_core.models.transport_model``."""

import sys

from harness_core.models import transport_model as _moved
from harness_core.models.transport_model import *  # noqa: F403

sys.modules[__name__] = _moved
