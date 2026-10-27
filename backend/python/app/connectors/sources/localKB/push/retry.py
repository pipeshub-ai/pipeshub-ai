"""Which graph failures mean "this attempt's transaction is gone; decide again".

The push's graph write is one explicit transaction. When the store ends it from under us (an
ArangoDB stream transaction aborted by the server, a lock timeout, a deadlock, a write-write
conflict; a Neo4j transient error), nothing of the attempt is applied, so the push re-reads the
record and decides again, keeping the storage document it already created. This is the push's one
retry loop (``KbPushService._write``), bounded in attempts and in total time.

Connector code must not import a storage client, so the errors are recognised by what they carry:
an ArangoDB query error has an integer ``error_num`` and the HTTP ``status``; a Neo4j transient
error is a ``TransientError`` or has a ``code`` starting ``Neo.TransientError.``. The providers wrap a failed statement in
their own error type, so the cause chain is walked.
"""

import re

#: lock timeout, deadlock, write-write conflict, transaction aborted, transaction not found
ARANGO_RETRYABLE_CODES = frozenset({18, 29, 1200, 1654, 1655})
_ARANGO_GONE = 410
_NEO4J_TRANSIENT_PREFIX = "Neo.TransientError."
# Batch writes report an ArangoDB conflict only in their message, in exactly this form (arango_http_client):
# "Batch insert failed with 1 error(s): Item 0: [1200] timeout waiting to lock key". Anchored to that prefix,
# so a bracketed number elsewhere ("blocks[18]", a quoted value) is not taken for one.
_BATCH_PREFIX = re.compile(r"Batch \w+ failed with \d+ error\(s\): ")
_BATCH_ITEM = re.compile(r"Item \d+: \[(?:18|29|1200|1654|1655)\]")


def _is_loss(error: BaseException) -> bool:
    code = getattr(error, "code", None)
    if isinstance(code, str) and code.startswith(_NEO4J_TRANSIENT_PREFIX):
        return True
    if any(cls.__name__ == "TransientError" for cls in type(error).__mro__):  # Neo4j's, raised without a code
        return True
    if hasattr(error, "error_num"):  # an ArangoDB query error; error_num is None when the body had none
        return getattr(error, "error_num", None) in ARANGO_RETRYABLE_CODES or getattr(error, "status", None) == _ARANGO_GONE
    message = str(error)
    prefix = _BATCH_PREFIX.match(message)
    return prefix is not None and _BATCH_ITEM.search(message, prefix.end()) is not None


def is_attempt_lost(error: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if _is_loss(current):
            return True
        current = current.__cause__ or current.__context__
    return False
