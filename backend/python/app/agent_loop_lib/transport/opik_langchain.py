"""Legacy import path; this module lives in ``harness_core.models.transports.opik_langchain``."""

import sys

from harness_core.models.transports import opik_langchain as _moved
from harness_core.models.transports.opik_langchain import *  # noqa: F403

sys.modules[__name__] = _moved
