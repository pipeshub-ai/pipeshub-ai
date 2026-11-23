"""Settings the messaging unit tests share."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _repurge_chain_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """The paid-result re-purge tests drive the delayed hops, which are published only when the operator turns the
    chain on (``PAID_RESULT_REPURGE_CHAIN``); the default-off behaviour is tested by deleting the variable."""
    monkeypatch.setenv("PAID_RESULT_REPURGE_CHAIN", "true")
