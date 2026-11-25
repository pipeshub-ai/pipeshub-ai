"""Legacy import path; this module lives in ``harness_core.models.transports.ollama``."""

import sys

from harness_core.models.transports import ollama as _moved
from harness_core.models.transports.ollama import *  # noqa: F403

sys.modules[__name__] = _moved
