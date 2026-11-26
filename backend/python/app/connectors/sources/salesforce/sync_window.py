"""Where an incremental Salesforce read starts, and the SOQL datetime literal for it.

A step's checkpoint is the time it *started* reading, less an overlap, never the time it
finished: a record saved while the step was reading carries a timestamp from before the
step ended, and a checkpoint taken at the end would skip it for good. Salesforce stamps a
record when its transaction starts, and the PipesHub host's clock can drift from
Salesforce's, so the overlap covers both (getUpdated/getDeleted report a
``latestDateCovered`` for the same reason). Re-reading a record inside the overlap is
harmless: an unchanged one is not written again.
"""

from datetime import datetime, timezone

#: How far before a step's start the next read begins.
CHECKPOINT_OVERLAP_MS = 5 * 60 * 1000


def checkpoint_for(started_ms: int) -> int:
    """The checkpoint to store for a step that started reading at ``started_ms``."""
    return max(0, started_ms - CHECKPOINT_OVERLAP_MS)


def soql_datetime(epoch_ms: int) -> str:
    """A SOQL dateTime literal in the documented form ``YYYY-MM-DDThh:mm:ssZ``.

    Rounded down to the second: SOQL documents only whole seconds, and a later bound
    would skip what changed in that second.
    """
    dt = datetime.fromtimestamp(int(epoch_ms) // 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
