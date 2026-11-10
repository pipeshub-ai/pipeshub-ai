"""pgvector search: which tier answered, what was degraded, how long searches waited for a slot, and the online recall sample.

Labels are small closed sets (tier, reason, size or share band); no org, query or collection is ever a label.
"""

from app.telemetry.backend import METRICS_BACKEND

_RECALL_BUCKETS = (0.0, 0.5, 0.8, 0.9, 0.95, 0.98, 0.99, 1.0)
_WAIT_BUCKETS = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05)

TIER = METRICS_BACKEND.counter(
    "pipeshub_pgvector_search_tier_total", "Dense legs by the tier that served them (e0, e0x, h, h_deep)", ["tier", "leg_band"])
DEGRADED = METRICS_BACKEND.counter(
    "pipeshub_pgvector_degraded_total",
    "Dense legs answered by the bounded exact path (reason: admission, timeout) or with rows missing (overload)", ["reason", "tier", "leg_band"])
EMPTY_LEG = METRICS_BACKEND.counter(
    "pipeshub_pgvector_empty_leg_total", "Dense legs that returned nothing because even the bounded exact path ran out of time", ["tier"])
ADMISSION_WAIT = METRICS_BACKEND.histogram(
    "pipeshub_pgvector_admission_wait_seconds", "Time a search waited for an admission slot", ["slot"], _WAIT_BUCKETS)
ADMISSION_REJECTED = METRICS_BACKEND.counter(
    "pipeshub_pgvector_admission_rejected_total", "Searches that found no free slot within the wait", ["slot"])
TIMEOUT = METRICS_BACKEND.counter(
    "pipeshub_pgvector_timeout_total", "Search legs that ran past their budget", ["leg", "tier"])
RECALL = METRICS_BACKEND.histogram(
    "pipeshub_pgvector_recall_sample", "Recall of sampled dense legs against an exact scan of the same candidates",
    ["tier", "size_band", "share_band", "leg_band"], _RECALL_BUCKETS)
SAMPLE_DROPPED = METRICS_BACKEND.counter(
    "pipeshub_pgvector_recall_sample_dropped_total", "Recall samples dropped (queue full, no slot, rate limit, timeout)", ["reason"])
ORG_RECALL_LOW = METRICS_BACKEND.counter(
    "pipeshub_pgvector_org_recall_low_total", "Times an organisation's recent sampled recall was below the floor", ["window"])
QUANTIZATION_RECALL = METRICS_BACKEND.histogram(
    "pipeshub_pgvector_quantization_recall", "Recall of bit HNSW plus rerank against exact search, per embedding-space check",
    ["verdict"], _RECALL_BUCKETS)


def leg_band(leg: int) -> str:
    return "40" if leg <= 40 else "120" if leg <= 120 else "600"
