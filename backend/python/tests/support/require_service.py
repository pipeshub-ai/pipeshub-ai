"""Missing-service handling for integration tests: skip on a laptop, fail in CI.

A suite that "passed" with its store down had skipped every store-backed test. When ``CI`` or ``REQUIRE_SERVICES`` is set
(a workflow sets it), a missing service is a failure.
"""

import os

import pytest


def missing(reason: str) -> None:
    if os.environ.get("CI") == "true" or os.environ.get("REQUIRE_SERVICES") == "1":
        pytest.fail(f"required service is missing in CI: {reason}", pytrace=False)
    pytest.skip(reason)
