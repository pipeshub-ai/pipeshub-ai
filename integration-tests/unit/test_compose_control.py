"""How the resilience tests find and drive the compose stack, without docker."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from helper import compose_control
from helper.compose_control import ComposeStack, ComposeUnavailable


def _done(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def compose_file(tmp_path: Path) -> Path:
    path = tmp_path / "docker-compose.integration.neo4j.yml"
    path.write_text("services: {}\n")
    return path


def test_defaults_to_the_integration_file_for_the_graph_under_test(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "docker-compose.integration.arango.yml").write_text("services: {}\n")
    monkeypatch.setattr(compose_control, "COMPOSE_DIR", tmp_path)
    monkeypatch.delenv("RESILIENCE_COMPOSE_FILE", raising=False)
    monkeypatch.delenv("RESILIENCE_COMPOSE_PROJECT", raising=False)
    monkeypatch.setenv("TEST_GRAPH_DB_TYPE", "arango")
    monkeypatch.setattr(compose_control.shutil, "which", lambda _: "/usr/bin/docker")

    stack = ComposeStack.from_env()

    assert stack.compose_file == (tmp_path / "docker-compose.integration.arango.yml").resolve()
    assert stack.project is None


def test_a_missing_compose_file_is_a_reason_not_a_crash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RESILIENCE_COMPOSE_FILE", str(tmp_path / "nope.yml"))

    with pytest.raises(ComposeUnavailable, match="does not exist"):
        ComposeStack.from_env()


def test_no_docker_cli_is_a_reason(monkeypatch: pytest.MonkeyPatch, compose_file: Path) -> None:
    monkeypatch.setenv("RESILIENCE_COMPOSE_FILE", str(compose_file))
    monkeypatch.setattr(compose_control.shutil, "which", lambda _: None)

    with pytest.raises(ComposeUnavailable, match="not on PATH"):
        ComposeStack.from_env()


def test_commands_target_the_file_and_project_from_its_folder(compose_file: Path) -> None:
    stack = ComposeStack(compose_file, project="ci-stack")
    with mock.patch.object(compose_control.subprocess, "run", return_value=_done()) as run:
        stack.restart("redis")

    argv = run.call_args.args[0]
    assert argv == ["docker", "compose", "-f", str(compose_file), "-p", "ci-stack", "restart", "redis"]
    assert run.call_args.kwargs["cwd"] == compose_file.parent


def test_require_names_the_services_that_are_not_running(compose_file: Path) -> None:
    stack = ComposeStack(compose_file)
    with mock.patch.object(compose_control.subprocess, "run", return_value=_done("redis\nqdrant\n")):
        stack.require("redis", "qdrant")
        with pytest.raises(ComposeUnavailable, match="pipeshub-ai"):
            stack.require("redis", "pipeshub-ai")


def test_an_unreachable_daemon_is_a_reason(compose_file: Path) -> None:
    stack = ComposeStack(compose_file)
    failure = _done(returncode=1, stderr="Cannot connect to the Docker daemon")
    with mock.patch.object(compose_control.subprocess, "run", return_value=failure):
        with pytest.raises(ComposeUnavailable, match="Cannot connect to the Docker daemon"):
            stack.running_services()


def test_wait_ready_polls_until_the_health_check_passes(compose_file: Path) -> None:
    stack = ComposeStack(compose_file)
    states = iter(["starting", "starting", "healthy"])

    def fake_run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if argv[:2] == ["docker", "inspect"]:
            return _done(json.dumps({"Status": "running", "Health": {"Status": next(states)}}))
        return _done("abc123\n")

    with mock.patch.object(compose_control.subprocess, "run", side_effect=fake_run), \
            mock.patch.object(compose_control.time, "sleep"):
        stack.wait_ready("qdrant", timeout=60)


def _no_health_check(probe_results: list[int]):
    calls: list[list[str]] = []
    results = iter(probe_results)

    def fake_run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[:2] == ["docker", "inspect"]:
            return _done(json.dumps({"Status": "running"}))
        if "exec" in argv:
            return _done(returncode=next(results))
        return _done("abc123\n")

    return fake_run, calls


def test_running_is_not_ready_without_a_health_check_or_probe(compose_file: Path) -> None:
    stack = ComposeStack(compose_file)
    fake_run, _ = _no_health_check([])

    with mock.patch.object(compose_control.subprocess, "run", side_effect=fake_run):
        assert stack.health("redis") == "running"
        with pytest.raises(ComposeUnavailable, match="no health check"):
            stack.wait_ready("redis")


def test_without_a_health_check_the_probe_decides(compose_file: Path) -> None:
    stack = ComposeStack(compose_file)
    fake_run, calls = _no_health_check([1, 1, 0])

    with mock.patch.object(compose_control.subprocess, "run", side_effect=fake_run), \
            mock.patch.object(compose_control.time, "sleep"):
        stack.wait_ready("redis", probe=["redis-cli", "ping"], timeout=60)

    probes = [argv for argv in calls if "exec" in argv]
    assert len(probes) == 3
    assert probes[-1][-5:] == ["exec", "-T", "redis", "redis-cli", "ping"]


def test_graph_service_follows_the_graph_backend_under_test(monkeypatch) -> None:
    monkeypatch.setenv("TEST_GRAPH_DB_TYPE", "Neo4j")
    assert compose_control.graph_service() == "neo4j"
    monkeypatch.setenv("TEST_GRAPH_DB_TYPE", "arango")
    assert compose_control.graph_service() == "arango"


def test_stop_confirms_the_service_is_really_down(compose_file: Path) -> None:
    stack = ComposeStack(compose_file)
    calls: list[list[str]] = []

    def still_running(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return _done("mongodb\nredis\n" if "ps" in argv else "")

    with mock.patch.object(compose_control.subprocess, "run", side_effect=still_running):
        with pytest.raises(ComposeUnavailable, match="still reports running"):
            stack.stop("mongodb")
    assert any(argv[-2:] == ["stop", "mongodb"] for argv in calls)

    with mock.patch.object(compose_control.subprocess, "run", return_value=_done("redis\n")):
        stack.stop("mongodb")
