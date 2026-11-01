"""Switches for how knowledge results are shown to the agent.

Each follows HIGH_ACCURACY_MODE when unset or blank and decides for its own
feature alone when set (see `accuracy_feature_enabled`). The deployment sets
them; a request never does.

- DUPLICATE_BLOCK_ELISION: a search result leaves out the blocks the
  first-turn context, an earlier fetch or an earlier search already shows,
  and says where they are.
"""

from __future__ import annotations

from app.utils.env_utils import accuracy_feature_enabled

DUPLICATE_BLOCK_ELISION_ENV_VAR = "DUPLICATE_BLOCK_ELISION"


def duplicate_block_elision_enabled() -> bool:
    return accuracy_feature_enabled(DUPLICATE_BLOCK_ELISION_ENV_VAR)
