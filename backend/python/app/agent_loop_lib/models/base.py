"""Legacy import path; this module lives in ``harness_core.models.base``."""

import sys

from harness_core.models import base as _moved
from harness_core.models.base import *  # noqa: F403

sys.modules[__name__] = _moved
