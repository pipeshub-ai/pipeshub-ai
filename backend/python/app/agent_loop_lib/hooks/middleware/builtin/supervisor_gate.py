"""Legacy import path; this module lives in ``harness_core.kernel.hooks.builtin.supervisor_gate``."""

import sys

from harness_core.kernel.hooks.builtin import supervisor_gate as _moved
from harness_core.kernel.hooks.builtin.supervisor_gate import *  # noqa: F403

sys.modules[__name__] = _moved
