"""Each case starts a process: the policy-store mode is read once per process."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.harness.policy_documents import policy_store_mode

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _fresh_policy_store_mode() -> Iterator[None]:
    policy_store_mode.cache_clear()
    yield
    policy_store_mode.cache_clear()
