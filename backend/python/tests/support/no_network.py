"""No test reaches past the host unless it says it needs to (H9).

A process-wide guard on ``socket.socket.connect`` / ``connect_ex``: loopback, Unix sockets and non-IP families
pass; any other address is a dial. A dial is recorded with the running test, the address, the calling thread
(and the test that started it) and the stack. Tests marked ``network`` (or ``docker`` / ``integration``, lanes
against real services by definition) may dial.

``TEST_NETWORK_GUARD`` picks the mode: ``fail`` (default) refuses the dial and fails the test even when the code
under test swallowed the refusal; ``report`` lets it through and only records; ``off`` installs nothing.
``TEST_NETWORK_GUARD_REPORT`` names a JSON-lines file every dial is appended to (each xdist worker appends).

A dial made outside the running test (at collection, or from a thread an earlier test left running) is never charged
to whichever test runs next: it fails the session instead, listed in the terminal summary.

Tests that patch ``socket.socket.connect`` themselves (``test_model_egress``, the web and Slack behaviour suites)
replace this guard for their duration and put it back on teardown; the guard holds no per-test patch to undo.
"""

from __future__ import annotations

import asyncio
import dataclasses
import errno
import ipaddress
import json
import os
import socket
import threading
import traceback
from typing import TYPE_CHECKING, Any, cast

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterator

MODE_ENV = "TEST_NETWORK_GUARD"
REPORT_ENV = "TEST_NETWORK_GUARD_REPORT"
MARKER = "network"
_OPT_IN_MARKERS = (MARKER, "docker", "integration")
_LOCAL_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"})
_STARTED_BY = "_network_guard_started_by"
_STACK_LIMIT = 30


class NetworkDialRefused(ConnectionRefusedError):
    """The guard's refusal: a ``ConnectionRefusedError`` so the code under test sees an offline host."""


@dataclasses.dataclass(frozen=True)
class Dial:
    test: str | None
    address: str
    thread: str
    thread_started_by: str | None
    stack: str

    def describe(self) -> str:
        where = self.test or "outside any test (collection, or a thread left running)"
        started = f", started during {self.thread_started_by}" if self.thread_started_by else ""
        return f"dial to {self.address} during {where} from thread {self.thread!r}{started}\n{self.stack}"


def is_local(family: int, address: object) -> bool:
    """Whether connecting a socket of ``family`` to ``address`` stays on this host."""
    if family not in (socket.AF_INET, socket.AF_INET6):
        return True
    if not isinstance(address, tuple) or not address:
        return False
    fields = cast("tuple[object, ...]", address)
    host = fields[0]
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        name = host.rstrip(".").lower()
        return name in _LOCAL_NAMES or name.endswith(".localhost")


def _await_chain(coro: Any) -> list[str]:  # noqa: ANN401
    chain: list[str] = []
    while coro is not None:
        frame = getattr(coro, "cr_frame", None) or getattr(coro, "ag_frame", None) or getattr(coro, "gi_frame", None)
        if frame is not None:
            chain.append(f"  {frame.f_code.co_filename}:{frame.f_lineno} in {frame.f_code.co_name}")
        coro = getattr(coro, "cr_await", None) or getattr(coro, "ag_await", None) or getattr(coro, "gi_yieldfrom", None)
    return chain


def _task_chains() -> str:
    """A dial made inside an event loop runs in a task the client spawned, whose thread stack shows only the loop;
    the await chains of the loop's tasks show the code under test that is waiting on it."""
    try:
        tasks = asyncio.all_tasks()
    except RuntimeError:
        return ""
    return "".join(
        f"task {task.get_name()}:\n" + "\n".join(_await_chain(task.get_coro())) + "\n" for task in tasks
    )


_installed: NetworkGuard | None = None


class NetworkGuard:
    """Installed once per process; also the pytest plugin that ties dials to tests."""

    def __init__(self, mode: str, report_path: str | None = None) -> None:
        if mode not in ("fail", "report"):
            raise ValueError(f"{MODE_ENV} must be fail, report or off, not {mode!r}")
        self.mode = mode
        self._report_path = report_path
        self._lock = threading.Lock()
        self._pending: list[Dial] = []
        self._orphans: list[Dial] = []
        self._test: str | None = None
        self._allowed = False
        self._real_connect: Callable[..., Any] | None = None
        self._real_connect_ex: Callable[..., Any] | None = None
        self._real_thread_start: Callable[..., Any] | None = None

    @classmethod
    def from_env(cls) -> NetworkGuard | None:
        mode = os.environ.get(MODE_ENV, "fail").strip().lower()
        if mode == "off":
            return None
        return cls(mode, os.environ.get(REPORT_ENV) or None)

    def register(self, config: pytest.Config) -> None:
        config.pluginmanager.register(self, "no_network")

    def install(self) -> None:
        # A second guard would wrap the first and, never registered as the plugin, refuse every dial: a conftest
        # imported by name runs again as another module and lands here.
        global _installed  # noqa: PLW0603 - one guard per process
        if _installed is not None:
            raise RuntimeError("a NetworkGuard is already installed in this process; import shared helpers from "
                               "tests/support, never a conftest by name")
        _installed = self
        real_connect = self._real_connect = socket.socket.connect
        real_connect_ex = self._real_connect_ex = socket.socket.connect_ex
        real_start = self._real_thread_start = threading.Thread.start
        guard = self

        def connect(sock: socket.socket, address: Any) -> None:  # noqa: ANN401
            if guard._admit(sock, address):
                return real_connect(sock, address)
            raise NetworkDialRefused(errno.ECONNREFUSED, f"unit tests do not reach the network: {address!r}")

        def connect_ex(sock: socket.socket, address: Any) -> int:  # noqa: ANN401
            if guard._admit(sock, address):
                return real_connect_ex(sock, address)
            return errno.ECONNREFUSED

        def start(thread: threading.Thread) -> None:
            setattr(thread, _STARTED_BY, guard._test)
            real_start(thread)

        socket.socket.connect = connect  # type: ignore[method-assign]
        socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
        threading.Thread.start = start  # type: ignore[method-assign]

    def uninstall(self) -> None:
        global _installed  # noqa: PLW0603 - one guard per process
        if _installed is self:
            _installed = None
        if self._real_connect is not None:
            socket.socket.connect = self._real_connect  # type: ignore[method-assign]
            socket.socket.connect_ex = self._real_connect_ex  # type: ignore[method-assign]
            threading.Thread.start = self._real_thread_start  # type: ignore[method-assign]
            self._real_connect = None

    def _admit(self, sock: socket.socket, address: object) -> bool:
        if is_local(sock.family, address):
            return True
        with self._lock:
            test, allowed = self._test, self._allowed
        if allowed:
            return True
        thread = threading.current_thread()
        stack = "".join(traceback.format_stack(limit=_STACK_LIMIT)[:-2]) + _task_chains()
        dial = Dial(test, repr(address), thread.name, getattr(thread, _STARTED_BY, None), stack)
        with self._lock:
            self._pending.append(dial)
        self._write(dial)
        return self.mode == "report"

    def _write(self, dial: Dial) -> None:
        if self._report_path is None:
            return
        line = json.dumps({"mode": self.mode, "worker": os.environ.get("PYTEST_XDIST_WORKER"), **dataclasses.asdict(dial)})
        with open(self._report_path, "a", encoding="utf-8") as report:
            report.write(line + "\n")

    def _drain(self) -> list[Dial]:
        with self._lock:
            dials, self._pending = self._pending, []
        return dials

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item: pytest.Item) -> Iterator[None]:
        allowed = any(item.get_closest_marker(name) for name in _OPT_IN_MARKERS)
        with self._lock:
            self._test, self._allowed = item.nodeid, allowed
        try:
            yield
        finally:
            with self._lock:
                self._test, self._allowed = None, False

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(
        self, item: pytest.Item, call: pytest.CallInfo[None]
    ) -> Generator[None, pytest.TestReport, pytest.TestReport]:
        report = yield
        dials: list[Dial] = []
        for dial in self._drain():
            mine = dial.test == item.nodeid and dial.thread_started_by in (None, item.nodeid)
            (dials if mine else self._orphans).append(dial)
        if not dials:
            return report
        text = "\n".join(dial.describe() for dial in dials)
        report.sections.append((f"network dials ({call.when})", text))
        if self.mode == "fail" and not report.failed:
            report.outcome = "failed"
            report.longrepr = f"{len(dials)} network dial(s) refused; mark the test `network` if it must dial\n{text}"
        return report

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        self._orphans += self._drain()
        if not self._orphans or self.mode != "fail":
            return
        if session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
        if hasattr(session.config, "workeroutput"):
            # An xdist worker's exit status never reaches the controller; `shouldfail` does, and fails the run.
            session.config.workeroutput["shouldfail"] = self._orphan_summary()

    def pytest_terminal_summary(self, terminalreporter: pytest.TerminalReporter) -> None:
        if self._orphans:
            terminalreporter.section("network dials outside their test")
            terminalreporter.write_line(self._orphan_summary())

    def _orphan_summary(self) -> str:
        text = "\n".join(dial.describe() for dial in self._orphans)
        return f"{len(self._orphans)} network dial(s) made outside the test that ran\n{text}"
