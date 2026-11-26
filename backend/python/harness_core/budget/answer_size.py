"""How large an answer-only turn may be (BUDGETEND): a leaf both the wrap-up reserve (its price) and the run budget
(its time, WP82-FU-3) read, so neither imports the other."""

from __future__ import annotations

from typing import Final

# The wrap-up request's prompt is cut to this many tokens (the system prompt and tools included), and its answer
# to this many output tokens: the reserve is their price.
WRAP_UP_INPUT_TOKENS: Final = 24_000
WRAP_UP_OUTPUT_TOKENS: Final = 8_000

__all__ = ["WRAP_UP_INPUT_TOKENS", "WRAP_UP_OUTPUT_TOKENS"]
