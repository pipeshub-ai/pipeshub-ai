"""Switches and knobs for the second-stage record reranker.

``RECORD_RERANK`` is off unless set to ``true``; ``HIGH_ACCURACY_MODE`` does
not turn it on. The knobs are read per search, so a change needs no restart.

Strategy-specific settings (which model to call) belong to the strategy that
reads them, so a new strategy adds none here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.utils.env_config import env_float
from app.utils.env_utils import env_bool, env_int

RECORD_RERANK_ENV_VAR = "RECORD_RERANK"
RECORD_RERANK_STRATEGY_ENV_VAR = "RECORD_RERANK_STRATEGY"
RECORD_RERANK_POOL_ENV_VAR = "RECORD_RERANK_POOL"
RECORD_RERANK_TOP_ENV_VAR = "RECORD_RERANK_TOP"
RECORD_RERANK_KEEP_TOP_ENV_VAR = "RECORD_RERANK_KEEP_TOP"
RECORD_RERANK_TIMEOUT_ENV_VAR = "RECORD_RERANK_TIMEOUT_S"
RECORD_RERANK_LOOKUP_TIMEOUT_ENV_VAR = "RECORD_RERANK_LOOKUP_TIMEOUT_S"
RECORD_RERANK_TEXT_CHARS_ENV_VAR = "RECORD_RERANK_TEXT_CHARS"

DEFAULT_STRATEGY = "llm_listwise"
MAX_POOL = 500
MAX_TOP = 50
MAX_TEXT_CHARS = 2000


@dataclass(frozen=True)
class RecordRerankSettings:
    strategy: str = DEFAULT_STRATEGY
    pool: int = 100
    """Records offered to the reranker, best first by the fused search."""
    top: int = 8
    """Records the reranker places; the rest keep the fused order behind them."""
    keep_top: int = 1
    """The fused first ``keep_top`` records stay within ``top`` (in its last
    slots when the reranker left them out); 0 keeps none. Protecting more
    than the first record costs the reranker more of its own picks than it
    saves."""
    timeout_s: float = 6.0
    """Bound on the strategy's call; past it the fused order stands."""
    lookup_timeout_s: float = 10.0
    """Bound on reading the pool's titles and summaries, before that call."""
    text_chars: int = 300
    """Cap on the text shown beside each title: more costs tokens and latency
    on every reranked search for little gain."""


def record_rerank_enabled() -> bool:
    return env_bool(RECORD_RERANK_ENV_VAR, False)


def _bounded(name: str, default: int, low: int, high: int) -> int:
    value = env_int(name, default, lo=low, hi=high)
    return default if value is None else value


def record_rerank_settings() -> RecordRerankSettings:
    defaults = RecordRerankSettings()
    strategy = (os.getenv(RECORD_RERANK_STRATEGY_ENV_VAR) or "").strip().lower()
    return RecordRerankSettings(
        strategy=strategy or defaults.strategy,
        pool=_bounded(RECORD_RERANK_POOL_ENV_VAR, defaults.pool, 2, MAX_POOL),
        top=_bounded(RECORD_RERANK_TOP_ENV_VAR, defaults.top, 1, MAX_TOP),
        keep_top=_bounded(RECORD_RERANK_KEEP_TOP_ENV_VAR, defaults.keep_top, 0, MAX_TOP),
        timeout_s=env_float(RECORD_RERANK_TIMEOUT_ENV_VAR, defaults.timeout_s, low=0.1, high=60.0),
        lookup_timeout_s=env_float(
            RECORD_RERANK_LOOKUP_TIMEOUT_ENV_VAR, defaults.lookup_timeout_s, low=0.1, high=60.0,
        ),
        text_chars=_bounded(RECORD_RERANK_TEXT_CHARS_ENV_VAR, defaults.text_chars, 0, MAX_TEXT_CHARS),
    )
