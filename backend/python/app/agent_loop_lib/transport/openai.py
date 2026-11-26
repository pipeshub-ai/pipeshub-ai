"""Legacy import path; this module lives in ``harness_core.models.transports.openai``."""

import sys

from harness_core.models.transports import openai as _moved
from harness_core.models.transports.openai import *  # noqa: F403

sys.modules[__name__] = _moved
