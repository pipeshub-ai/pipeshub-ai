"""The stall bound X of a records schema widening (measured by ``test_record_diagnostics_save_transaction_arango.py``).

X = the 99th-percentile duration of a connector save transaction, measured there under a connector-like workload, plus
1 s. The widening waits for the open stream transactions that wrote ``records`` and every later ``records`` write queues
behind it; the quiet-point check keeps that from happening, and X is what a write may wait in the case the check does
not find a quiet moment (``pipeshub_record_schema_rewiden_contended_total``)."""

STALL_BOUND_S = 2.0
