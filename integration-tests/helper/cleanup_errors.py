"""A distinct assertion for the post-delete cleanup probes.

Deleting a record has to clear four stores. The cleanup tests mark the stores
known to keep data as expected failures, and those markers must fire only on
the post-delete "the data is still here" assertion -- never on a step that
proves the data existed *before* the delete. If a failed precondition could
also satisfy the marker, a record whose blobs or Mongo documents have not been
written yet (the indexing fixture waits only for embeddings), or a storage
backend the probe cannot read, would be recorded as the known product bug and
the delete would never actually be exercised.

``StoreNotEmptied`` is that post-delete failure. It subclasses ``AssertionError``
so it still reads as an assertion and is caught by a generic
``pytest.raises(AssertionError)``, but it is a distinct type, so a test can
xfail it alone with ``raises=StoreNotEmptied`` and leave a plain
``AssertionError`` from a precondition to fail the test for real.
"""

from __future__ import annotations


class StoreNotEmptied(AssertionError):
    """A store still holds a deleted record's data after the delete."""
