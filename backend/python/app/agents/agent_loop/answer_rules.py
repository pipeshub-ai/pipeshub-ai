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
- MULTI_STEP_ANSWER_RULES: every prompt that grants knowledge retrieval
  gets rules for questions that chain several lookups.
"""

from __future__ import annotations

from app.utils.env_utils import accuracy_feature_enabled

ANSWER_COMPOSITION_RULES_ENV_VAR = "ANSWER_COMPOSITION_RULES"
THOROUGH_RETRIEVAL_ENV_VAR = "THOROUGH_RETRIEVAL"
MULTI_STEP_ANSWER_RULES_ENV_VAR = "MULTI_STEP_ANSWER_RULES"

# Set by `chat_modes.bridge` for an enterprise-search chat under
# THOROUGH_RETRIEVAL, so the fetch policy in Finding Information agrees with
# the mode text instead of telling the model one block is enough.
READ_RECORDS_IN_FULL_STATE_KEY = "read_records_in_full"

# Names no tool, so it holds for a flat search tool and for the exploration
# delegate alike.
MULTI_STEP_ANSWER_RULES = (
    "Multi-step questions:\n"
    "- Before answering, confirm each stated constraint (dates, ranges, tolerances, "
    "locations, \"the first\", units) against a retrieved value or tool result. If one "
    "can't be confirmed, keep searching or name the unconfirmed part; don't round a "
    "near-miss into a match.\n"
    "- Once a step resolves an intermediate entity, search with that entity, not the "
    "original wording.\n"
    "- Before concluding something isn't in the knowledge base, try other readings of "
    "relative time anchors (\"the quarter before\") and re-check records already found.\n"
    "- For an attribute of a specific person, project, customer or product, prefer its "
    "own record over passing mentions; if they conflict, say so.\n"
    "- For an ambiguous time anchor, answer with the reading the records best support "
    "and name the alternative."
)


def answer_composition_rules_enabled() -> bool:
    return accuracy_feature_enabled(ANSWER_COMPOSITION_RULES_ENV_VAR)


def thorough_retrieval_enabled() -> bool:
    return accuracy_feature_enabled(THOROUGH_RETRIEVAL_ENV_VAR)


def multi_step_answer_rules() -> str | None:
    """The multi-step block, or None while MULTI_STEP_ANSWER_RULES is off."""
    if not accuracy_feature_enabled(MULTI_STEP_ANSWER_RULES_ENV_VAR):
        return None
    return MULTI_STEP_ANSWER_RULES
