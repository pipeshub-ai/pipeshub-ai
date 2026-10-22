"""Checks for calls that must fail."""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from pipeshub_sdk import errors


@contextmanager
def raises_status(status: int) -> Iterator[None]:
    """Check that the call in the block fails with the given HTTP status."""
    with pytest.raises(errors.PipeshubError) as raised:
        yield
    assert raised.value.status_code == status, str(raised.value)
