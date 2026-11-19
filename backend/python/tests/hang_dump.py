"""Keep the faulthandler_timeout stack dump armed through the teardown of a test that failed."""

import faulthandler

import pytest
from _pytest.faulthandler import fault_handler_stderr_fd_key


@pytest.hookimpl(trylast=True)
def pytest_exception_interact(node: pytest.Item | pytest.Collector, call: pytest.CallInfo, report: pytest.TestReport) -> None:
    # pytest's faulthandler plugin cancels its dump on any failure, so a fixture teardown that then hangs in C printed nothing.
    # Re-armed only for a test: its protocol cancels the timer when it ends. Dump only, as the plugin's own timer here.
    if not node.config.pluginmanager.hasplugin("faulthandler"):
        return  # -p no:faulthandler: its ini key and stderr fd do not exist
    timeout = float(node.config.getini("faulthandler_timeout") or 0.0)
    if timeout > 0 and isinstance(node, pytest.Item) and not node.config.getoption("usepdb", False):
        faulthandler.dump_traceback_later(timeout, file=node.config.stash[fault_handler_stderr_fd_key], exit=False)
