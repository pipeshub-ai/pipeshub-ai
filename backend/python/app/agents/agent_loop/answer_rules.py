"""Opt-in rules for how a knowledge answer is gathered and written.

Each switch follows HIGH_ACCURACY_MODE when unset or blank, and decides for
its own rules alone when set (see `accuracy_feature_enabled`). The deployment
sets them; they are never read from a request, so the prompt text they add
is the same for every question and stays inside the cached prompt prefix.

- ANSWER_COMPOSITION_RULES: enterprise-search answers name what they are
  about and who said it, keep values with their qualifiers, answer from the
  record that defines a procedure, and resolve conflicting sources in a
  fixed order. Prompt text only.
- THOROUGH_RETRIEVAL: enterprise search reads the records an answer rests
  on in full, searches again for any part the context does not state, and
  looks for other sources on a question about a conflict. More fetch and
  search turns per question.
"""

from __future__ import annotations

from app.utils.env_utils import accuracy_feature_enabled

ANSWER_COMPOSITION_RULES_ENV_VAR = "ANSWER_COMPOSITION_RULES"
THOROUGH_RETRIEVAL_ENV_VAR = "THOROUGH_RETRIEVAL"

# Set by `chat_modes.bridge` for an enterprise-search chat under
# THOROUGH_RETRIEVAL, so the fetch policy in Finding Information agrees with
# the mode text instead of telling the model one block is enough.
READ_RECORDS_IN_FULL_STATE_KEY = "read_records_in_full"


def answer_composition_rules_enabled() -> bool:
    return accuracy_feature_enabled(ANSWER_COMPOSITION_RULES_ENV_VAR)


def thorough_retrieval_enabled() -> bool:
    return accuracy_feature_enabled(THOROUGH_RETRIEVAL_ENV_VAR)

