"""Legacy import path; this module lives in ``harness_core.kernel.agent.prompt_template``."""

import sys

from harness_core.kernel.agent import prompt_template as _moved
from harness_core.kernel.agent.prompt_template import *  # noqa: F403

sys.modules[__name__] = _moved
