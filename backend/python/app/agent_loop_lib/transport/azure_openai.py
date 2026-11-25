"""Legacy import path; this module lives in ``harness_core.models.transports.azure_openai``."""

import sys

from harness_core.models.transports import azure_openai as _moved
from harness_core.models.transports.azure_openai import *  # noqa: F403

sys.modules[__name__] = _moved
