"""Indexing pipeline: refresh intents, the replay set, re-keyed vector blocks, page counts and the keep-old-copy check."""

from app.plugins.telemetry.backend import METRICS_BACKEND

SWAP_REFUSALS = METRICS_BACKEND.counter(
    "pipeshub_keep_old_copy_refused_total",
    "Runs that used the order they always had because the fleet could not be confirmed for keeping the old copy", ["reason"],
)

PAGE_COUNT_UNAVAILABLE = METRICS_BACKEND.counter(
    "pipeshub_pdf_page_count_unavailable_total",
    "PDFs whose page count could not be taken (a timeout, a dead worker or a closed pool)",
    [],
)

REFRESH_INTENT_EVENTS = METRICS_BACKEND.counter(
    "pipeshub_refresh_intent_total",
    "Force reindex requests kept on a record, by what happened to them",
    ["event"],
)

REKEYED = METRICS_BACKEND.counter(
    "pipeshub_vector_rekeyed_blocks_total",
    "Blocks whose stored points were moved to a content-derived id with their stored vectors, not embedded again", ["store"],
)

REKEY_FALLBACK = METRICS_BACKEND.counter(
    "pipeshub_vector_rekey_fallback_total",
    "Blocks embedded again because their stored points could not be moved to a content-derived id", ["reason"],
)

REPLAY_SET_ERRORS = METRICS_BACKEND.counter(
    "pipeshub_replay_set_errors_total",
    "Replay-set reads or writes that failed; the replay guard is weaker for that record, nothing else changes",
    ["op"],
)

REPLAYS_ACKNOWLEDGED = METRICS_BACKEND.counter(
    "pipeshub_replay_set_acknowledged_total",
    "Redelivered events of an already completed delivery acknowledged without work",
    ["service"],
)

EVICTION_POLICY_UNSAFE = METRICS_BACKEND.gauge(
    "pipeshub_redis_eviction_policy_unsafe",
    "1 when the Redis maxmemory-policy can evict the replay set, pause counters and budgets silently",
    ["service"],
)
