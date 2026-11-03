"""Indexing-time LLM calls, as the gateway sees them: outcomes, tokens and open circuits."""

from app.telemetry.backend import METRICS_BACKEND

LLM_CALLS = METRICS_BACKEND.counter(
    "pipeshub_indexing_llm_calls_total",
    "Indexing-time LLM calls by call site and outcome",
    ["call_site", "outcome"],
)
LLM_TOKENS = METRICS_BACKEND.counter(
    "pipeshub_indexing_llm_tokens_total",
    "Tokens sent to and received from indexing-time LLM calls",
    ["call_site", "direction"],
)
LLM_BREAKER_OPEN = METRICS_BACKEND.gauge(
    "pipeshub_indexing_llm_breaker_open",
    "1 while calls to a model provider fail fast",
    ["provider"],
)
