"""Legacy import path; this module lives in ``harness_core.models.transports.content_filter``."""

import sys

from harness_core.models.transports import content_filter as _moved
from harness_core.models.transports.content_filter import *  # noqa: F403

sys.modules[__name__] = _moved
