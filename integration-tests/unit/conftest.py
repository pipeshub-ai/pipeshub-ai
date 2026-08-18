"""Isolate helper unit tests from live-stack session fixtures.

Root conftest autouses ``local_oauth_credentials`` when
``PIPESHUB_TEST_ENV=local``. CI runs ``pytest unit/`` before Docker is up, so
that fixture must not hit localhost here.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def local_oauth_credentials() -> None:
    """No-op override of root conftest live OAuth bootstrap."""
    return
