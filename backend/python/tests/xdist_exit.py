"""Store pytest's status and force-exit before conftest does.

os._exit drops the status unless pytest_sessionfinish stored it, and it
skips the interpreter's own flush. A configure failure never reaches
sessionfinish; this hook then exits with an internal error.
"""

import os
import sys

import pytest


def pytest_sessionfinish(session, exitstatus: int) -> None:
    session.config._exitcode = int(exitstatus)


@pytest.hookimpl(tryfirst=True)
def pytest_unconfigure(config) -> None:
    """Run before conftest's force-exit so a configure failure cannot exit 0."""
    if os.environ.get("PYTEST_XDIST_WORKER") or getattr(config.option, "numprocesses", None):
        # A run that never reached sessionfinish did not pass.
        if not hasattr(config, "_exitcode"):
            config._exitcode = int(pytest.ExitCode.INTERNAL_ERROR)
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(int(config._exitcode))
