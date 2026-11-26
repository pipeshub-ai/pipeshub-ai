"""Legacy import path; this module lives in ``harness_core.models.transports.prompt_cache``."""

import sys

from harness_core.models.transports import prompt_cache as _moved
from harness_core.models.transports.prompt_cache import *  # noqa: F403

sys.modules[__name__] = _moved
