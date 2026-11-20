"""The answer label: what an answer's model calls were given, keyed by record id.

`label` holds the wire shape, `ledger` the request-scoped recorder and its
shared no-op default, `tool_class` how a tool's result restricts an answer,
and `inventory` every model-call site and prompt section with its class.
"""
