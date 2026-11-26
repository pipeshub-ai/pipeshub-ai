"""Legacy import path; this module lives in ``harness_core.kernel.context.compaction_memo``."""

import sys

from harness_core.kernel.context import compaction_memo as _moved
from harness_core.kernel.context.compaction_memo import *  # noqa: F403

sys.modules[__name__] = _moved
