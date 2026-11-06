"""Switches for how knowledge results are shown to the agent.

Each follows HIGH_ACCURACY_MODE when unset or blank and decides for its own
feature alone when set (see `accuracy_feature_enabled`). The deployment sets
them; a request never does.

- DUPLICATE_BLOCK_ELISION: a search result leaves out the blocks the
  first-turn context, an earlier fetch or an earlier search already shows,
  and says where they are.
- CONTEXT_GAP_FILL: when one or two plain-text blocks separate two passages
  of a record the agent is shown, they are shown too.
- CONTEXT_RECORD_SPREAD: when a knowledge search matches more passages than
  it shows, each record keeps a few before any record keeps more.
"""

from __future__ import annotations

from app.utils.env_utils import accuracy_feature_enabled

DUPLICATE_BLOCK_ELISION_ENV_VAR = "DUPLICATE_BLOCK_ELISION"
CONTEXT_GAP_FILL_ENV_VAR = "CONTEXT_GAP_FILL"
CONTEXT_RECORD_SPREAD_ENV_VAR = "CONTEXT_RECORD_SPREAD"


def duplicate_block_elision_enabled() -> bool:
    return accuracy_feature_enabled(DUPLICATE_BLOCK_ELISION_ENV_VAR)


def context_gap_fill_enabled() -> bool:
    return accuracy_feature_enabled(CONTEXT_GAP_FILL_ENV_VAR)


def context_record_spread_enabled() -> bool:
    return accuracy_feature_enabled(CONTEXT_RECORD_SPREAD_ENV_VAR)
