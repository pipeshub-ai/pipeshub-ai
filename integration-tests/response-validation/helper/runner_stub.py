"""Skip in CI the tests whose stand-in provider is served by the test process itself.

The stand-in listens on the runner's 127.0.0.1. Locally the API runs on the same host and reaches
it; in CI the API runs in a container, whose 127.0.0.1 is its own.
"""

from __future__ import annotations

import os

import pytest

REASON = "the API in CI runs in a container and cannot reach a stand-in served by the test runner"

needs_runner_stub = pytest.mark.skipif(bool(os.getenv("CI")), reason=REASON)


def skip_in_ci() -> None:
    """For a fixture that starts a stand-in: skip every test that uses it."""
    if os.getenv("CI"):
        pytest.skip(REASON)
