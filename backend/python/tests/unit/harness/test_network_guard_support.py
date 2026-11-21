"""H9: the root guard that keeps tests on the host (``tests/support/no_network.py``).

Each case runs a small generated suite in a child pytest wired the way ``tests/conftest.py`` wires the guard,
and reads the outcome from its JUnit report. Every non-loopback address is from TEST-NET-1 (192.0.2.0/24); an
allowed dial uses UDP, whose ``connect`` sends nothing.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.support.no_network import is_local

_PYTHON_ROOT = Path(__file__).resolve().parents[3]

_CONFTEST = """
from tests.support.no_network import NetworkGuard

_guard = NetworkGuard.from_env()
if _guard is not None:
    _guard.install()


def pytest_configure(config):
    config.addinivalue_line("markers", "network: may dial")
    config.addinivalue_line("markers", "docker: docker")
    config.addinivalue_line("markers", "integration: integration")
    if _guard is not None:
        _guard.register(config)
"""

_PRELUDE = """
import socket
import threading

import pytest


def tcp(address, family=socket.AF_INET):
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.settimeout(2)
        sock.connect(address)


def udp(address):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(address)
"""


@dataclass(frozen=True)
class Outcome:
    status: str
    message: str


@dataclass(frozen=True)
class Run:
    outcomes: dict[str, Outcome]
    dials: list[dict[str, Any]]
    returncode: int
    output: str


def _run(
    tmp_path: Path, body: str, *, mode: str = "fail", modules: dict[str, str] | None = None, workers: int = 0
) -> Run:
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -p no:cacheprovider\n")
    (tmp_path / "conftest.py").write_text(_CONFTEST)
    (tmp_path / "test_suite.py").write_text(_PRELUDE + textwrap.dedent(body))
    for name, source in (modules or {}).items():
        (tmp_path / name).write_text(textwrap.dedent(source))
    junit, report = tmp_path / "junit.xml", tmp_path / "dials.jsonl"
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(_PYTHON_ROOT),
        "TEST_NETWORK_GUARD": mode,
        "TEST_NETWORK_GUARD_REPORT": str(report),
    }
    parallel = ["-n", str(workers)] if workers else ["-p", "no:xdist"]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *parallel, "--rootdir", str(tmp_path), f"--junitxml={junit}", str(tmp_path)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120, check=False,
    )
    outcomes: dict[str, Outcome] = {}
    for case in ET.parse(junit).getroot().iter("testcase"):
        problem = next((child for child in case if child.tag in ("failure", "error", "skipped")), None)
        status = "passed" if problem is None else problem.tag
        outcomes[case.get("name", "")] = Outcome(status, "" if problem is None else problem.get("message", "") + (problem.text or ""))
    dials = [json.loads(line) for line in report.read_text().splitlines()] if report.exists() else []
    return Run(outcomes, dials, result.returncode, result.stdout + result.stderr)


class TestRefuses:
    def test_ng1_a_dial_is_refused_and_fails_the_test_even_when_swallowed(self, tmp_path: Path) -> None:
        run = _run(tmp_path, """
            def test_dials_and_swallows():
                try:
                    tcp(("192.0.2.1", 443))
                except ConnectionRefusedError:
                    pass

            def test_sees_an_offline_host():
                with pytest.raises(ConnectionRefusedError):
                    tcp(("192.0.2.1", 443))

            def test_connect_ex_answers_refused():
                import errno
                with socket.socket() as sock:
                    assert sock.connect_ex(("192.0.2.1", 443)) == errno.ECONNREFUSED
        """)
        for name in ("test_dials_and_swallows", "test_sees_an_offline_host", "test_connect_ex_answers_refused"):
            outcome = run.outcomes[name]
            assert outcome.status == "failure", name
            assert "192.0.2.1" in outcome.message and "mark the test `network`" in outcome.message
        dial = next(d for d in run.dials if d["test"].endswith("test_dials_and_swallows"))
        assert dial["address"] == "('192.0.2.1', 443)"
        assert dial["thread"] == "MainThread"
        assert "test_suite.py" in dial["stack"]

    def test_ng5_a_dial_from_a_thread_an_earlier_test_left_running_fails_the_session_not_the_running_test(
        self, tmp_path: Path
    ) -> None:
        run = _run(tmp_path, """
            go, done = threading.Event(), threading.Event()

            def _dial_later():
                go.wait(10)
                try:
                    udp(("192.0.2.7", 9))
                except OSError:
                    pass
                done.set()

            def test_a_starts_a_thread():
                threading.Thread(target=_dial_later, name="leftover", daemon=True).start()

            def test_b_runs_while_it_dials():
                go.set()
                assert done.wait(10)
        """)
        assert run.outcomes["test_a_starts_a_thread"].status == "passed"
        assert run.outcomes["test_b_runs_while_it_dials"].status == "passed"
        assert run.returncode == pytest.ExitCode.TESTS_FAILED
        assert "thread 'leftover', started during" in run.output and "test_a_starts_a_thread" in run.output

    @pytest.mark.parametrize("workers", [0, 2])
    def test_ng6_a_dial_at_collection_is_refused_and_fails_the_session_not_the_first_test(
        self, tmp_path: Path, workers: int
    ) -> None:
        run = _run(
            tmp_path,
            """
            import dials_on_import

            def test_first():
                assert dials_on_import.REFUSED
            """,
            modules={"dials_on_import.py": """
                import socket
                REFUSED = False
                try:
                    socket.socket(socket.AF_INET, socket.SOCK_DGRAM).connect(("192.0.2.9", 9))
                except ConnectionRefusedError:
                    REFUSED = True
            """},
            workers=workers,
        )
        assert run.outcomes["test_first"].status == "passed"
        assert run.returncode != pytest.ExitCode.OK
        assert "192.0.2.9" in run.output and "outside any test" in run.output


class TestAllows:
    def test_ng2_loopback_unix_and_local_names_pass(self, tmp_path: Path) -> None:
        run = _run(tmp_path, """
            import os
            import tempfile

            def _listen(family, host):
                server = socket.socket(family, socket.SOCK_STREAM)
                server.bind((host, 0))
                server.listen()
                return server

            def test_ipv4_loopback():
                with _listen(socket.AF_INET, "127.0.0.1") as server:
                    tcp(server.getsockname())

            def test_localhost_by_name():
                with _listen(socket.AF_INET, "127.0.0.1") as server:
                    tcp(("localhost", server.getsockname()[1]))

            def test_unix_socket():
                path = os.path.join(tempfile.mkdtemp(), "s")
                with socket.socket(socket.AF_UNIX) as server:
                    server.bind(path)
                    server.listen()
                    with socket.socket(socket.AF_UNIX) as client:
                        client.connect(path)
        """)
        assert {name: o.status for name, o in run.outcomes.items()} == dict.fromkeys(
            ("test_ipv4_loopback", "test_localhost_by_name", "test_unix_socket"), "passed"
        )
        assert run.dials == []

    def test_ng3_the_network_marker_and_lanes_against_real_services_may_dial(self, tmp_path: Path) -> None:
        run = _run(tmp_path, """
            @pytest.mark.network
            def test_marked_network():
                udp(("192.0.2.1", 9))

            @pytest.mark.docker
            def test_marked_docker():
                udp(("192.0.2.2", 9))

            @pytest.mark.integration
            def test_marked_integration():
                udp(("192.0.2.3", 9))

            def test_unmarked():
                udp(("192.0.2.4", 9))
        """)
        for name in ("test_marked_network", "test_marked_docker", "test_marked_integration"):
            assert run.outcomes[name].status == "passed", name
        assert run.outcomes["test_unmarked"].status == "failure"
        assert [d["address"] for d in run.dials] == ["('192.0.2.4', 9)"]

    def test_ng4_report_mode_lets_the_dial_through_and_records_it(self, tmp_path: Path) -> None:
        run = _run(tmp_path, """
            def test_unmarked():
                udp(("192.0.2.5", 9))
        """, mode="report")
        assert run.outcomes["test_unmarked"].status == "passed"
        assert [(d["mode"], d["address"]) for d in run.dials] == [("report", "('192.0.2.5', 9)")]

    def test_ng8_a_test_that_patches_connect_itself_gets_the_guard_back_after_it(self, tmp_path: Path) -> None:
        run = _run(tmp_path, """
            def test_a_patches_connect(monkeypatch):
                def refuse(sock, address):
                    raise ConnectionRefusedError("own patch")
                monkeypatch.setattr(socket.socket, "connect", refuse)
                with pytest.raises(ConnectionRefusedError, match="own patch"):
                    udp(("192.0.2.6", 9))

            def test_b_is_guarded_again():
                with pytest.raises(ConnectionRefusedError):
                    udp(("192.0.2.6", 9))
        """)
        assert run.outcomes["test_a_patches_connect"].status == "passed"
        assert run.outcomes["test_b_is_guarded_again"].status == "failure"

    def test_ng9_off_installs_nothing(self, tmp_path: Path) -> None:
        run = _run(tmp_path, """
            def test_connect_is_the_socket_modules_own():
                assert socket.socket.connect is socket.SocketType.connect
        """, mode="off")
        assert run.outcomes["test_connect_is_the_socket_modules_own"].status == "passed"

    def test_ng10_a_conftest_loaded_again_under_another_name_cannot_stack_a_guard_that_refuses_docker_dials(
        self, tmp_path: Path
    ) -> None:
        run = _run(tmp_path, """
            import importlib.util
            import pathlib

            def test_loading_the_conftest_again_is_refused():
                path = pathlib.Path(__file__).with_name("conftest.py")
                spec = importlib.util.spec_from_file_location("tests_conftest_again", path)
                with pytest.raises(RuntimeError, match="already installed"):
                    spec.loader.exec_module(importlib.util.module_from_spec(spec))

            @pytest.mark.docker
            def test_marked_docker_still_dials():
                udp(("192.0.2.7", 9))
        """)
        assert run.outcomes["test_loading_the_conftest_again_is_refused"].status == "passed"
        assert run.outcomes["test_marked_docker_still_dials"].status == "passed"


@pytest.mark.parametrize(
    ("family", "address", "local"),
    [
        (socket.AF_INET, ("127.0.0.1", 80), True),
        (socket.AF_INET, ("127.8.9.10", 80), True),
        (socket.AF_INET6, ("::1", 80, 0, 0), True),
        (socket.AF_INET6, ("fe80::1%eth0", 80, 0, 2), False),
        (socket.AF_INET, ("localhost", 80), True),
        (socket.AF_INET, ("api.localhost.", 80), True),
        (socket.AF_INET, ("10.215.0.2", 80), False),
        (socket.AF_INET, ("0.0.0.0", 80), False),
        (socket.AF_INET, ("example.com", 443), False),
        (socket.AF_INET, (b"127.0.0.1", 80), True),
        (socket.AF_UNIX, "/var/run/docker.sock", True),
    ],
)
def test_ng10_what_counts_as_local(family: int, address: object, local: bool) -> None:
    assert is_local(family, address) is local
