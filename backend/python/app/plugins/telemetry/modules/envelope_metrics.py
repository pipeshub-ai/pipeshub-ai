"""Broker message envelopes: what was accepted, refused or sent without a signature, and consumer inbox failures."""

from app.plugins.telemetry.backend import METRICS_BACKEND

CTX_MISSING = METRICS_BACKEND.counter(
    "pipeshub_envelope_ctx_missing_total",
    "Broker messages accepted without a signed ctx (upgrade window)",
    ["topic"],
)

STALE_BY_TYPE = METRICS_BACKEND.counter(
    "pipeshub_envelope_stale_by_type_total",
    "Validly signed broker messages older than the accepted signature age, by event type (a self-scheduled control "
    "message dropped for lag ends its chain)",
    ["topic", "event_type"],
)

UNVERIFIED = METRICS_BACKEND.counter(
    "pipeshub_envelope_unverified_total",
    "Signed broker messages processed without verification because no key is configured",
    ["topic"],
)

STALE = METRICS_BACKEND.counter(
    "pipeshub_envelope_stale_total",
    "Validly signed broker messages older than the accepted signature age",
    ["topic"],
)

REJECTED = METRICS_BACKEND.counter(
    "pipeshub_envelope_rejected_total",
    "Broker messages dropped because their envelope was rejected, by reason and (only once the signature verified) event "
    "type: a dropped self-scheduled control message ends its chain, so this is the place to see one go missing",
    ["topic", "reason", "event_type"],
)

ADMISSION_REFUSED = METRICS_BACKEND.counter(
    "pipeshub_envelope_admission_refused_total",
    "Stored admissions of a queue delivery that did not verify (forged, copied, edited, expired or made with a key no longer configured)",
    ["topic"],
)

STRIPPED = METRICS_BACKEND.counter(
    "pipeshub_envelope_stripped_total",
    "Broker messages without a ctx while signing is enabled (stripped or unsigned producer)",
    ["topic"],
)

UNSIGNED = METRICS_BACKEND.counter(
    "pipeshub_envelope_unsigned_total",
    "Messages sent unsigned while signing is enabled",
    ["topic", "reason"],
)

INBOX_ERRORS = METRICS_BACKEND.counter(
    "pipeshub_consumer_inbox_errors_total",
    "Consumer inbox reads or writes that failed; the message was processed as if new (at-least-once)",
    ["op"],
)
