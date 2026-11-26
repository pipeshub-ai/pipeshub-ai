"""Legacy import path; this module lives in ``harness_core.models.transports.openai_responses``."""

import sys

from harness_core.models.transports import openai_responses as _moved
from harness_core.models.transports.openai_responses import *  # noqa: F403

sys.modules[__name__] = _moved
