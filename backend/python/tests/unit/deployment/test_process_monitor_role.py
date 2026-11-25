"""PIPESHUB_ROLE: `all` starts what it always did; `parsing` starts only the parse processes."""

import pathlib
import subprocess
import time

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[5] / "deployment" / "process_monitor.sh"

# Every Python module and Node entry the full application starts.
ALL_PYTHON = {
    "app.embedding_main", "app.connectors_main", "app.query_main",
    "app.docling_main", "app.indexing_main",
}
PARSE_PYTHON = {"app.docling_main", "app.parsing_main"}
# The agent harness worker and its model gateway start with the full application unless the operator turns the
# harness off (AGENT_HARNESS_WORKER_ENABLED, ADR-OPS-06); a parsing replica never starts them.
HARNESS_PYTHON = {"app.harness_main", "app.model_gateway_main"}


def _run(tmp_path: pathlib.Path, env: dict[str, str], seconds: float = 5) -> tuple[int, set[str], bool, str]:
    """Run the real script against stub python/node/curl; return (exit code, modules started, node started, log)."""
    app = tmp_path / "app"
    (app / "python").mkdir(parents=True)
    (app / "backend").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    for name in ("python", "node"):
        stub = bin_dir / name
        # The monitor asks `python -m app.capacity facts` for the container's facts before it starts anything.
        stub.write_text(f'#!/bin/bash\ncase "$*" in *app.capacity*) exit 0 ;; esac\necho "{name} $*" >> {calls}\nexec sleep 60\n')
        stub.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text("#!/bin/bash\nexit 0\n")
    curl.chmod(0o755)
    script = tmp_path / "process_monitor.sh"
    script.write_text(SCRIPT.read_text().replace("/app/", f"{app}/"))
    proc = subprocess.Popen(
        ["bash", str(script)],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "CHECK_INTERVAL": "1", "STARTUP_POLL_SECONDS": "1", **env},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    time.sleep(seconds)
    code = proc.poll()
    if code is None:
        proc.terminate()  # the script's own trap stops its children
    out, _ = proc.communicate(timeout=20)
    lines = calls.read_text().splitlines() if calls.exists() else []
    modules = {ln.split("-m ", 1)[1].split()[0] for ln in lines if ln.startswith("python -m ")}
    node = any(ln.startswith("node ") for ln in lines)
    return (code if code is not None else -1), modules, node, out


def test_default_role_runs_the_full_application(tmp_path: pathlib.Path) -> None:
    _, modules, node, _ = _run(tmp_path, {})
    assert modules == ALL_PYTHON | HARNESS_PYTHON
    assert node


def test_default_role_with_the_harness_off_runs_what_it_always_did(tmp_path: pathlib.Path) -> None:
    _, modules, node, _ = _run(tmp_path, {"AGENT_HARNESS_WORKER_ENABLED": "false"})
    assert modules == ALL_PYTHON
    assert node


def test_all_with_parsing_service_flag_is_unchanged(tmp_path: pathlib.Path) -> None:
    _, modules, node, _ = _run(tmp_path, {"PIPESHUB_ROLE": "all", "USE_PARSING_SERVICE": "true"})
    assert modules == ALL_PYTHON | HARNESS_PYTHON | {"app.parsing_main", "app.extraction_main"}
    assert node


def test_parsing_role_starts_only_parse_processes(tmp_path: pathlib.Path) -> None:
    code, modules, node, out = _run(tmp_path, {"PIPESHUB_ROLE": "parsing"})
    assert modules == PARSE_PYTHON, out
    assert not node
    assert code == -1, "the monitor must keep running"


def test_parsing_role_ignores_the_use_parsing_service_flag(tmp_path: pathlib.Path) -> None:
    _, modules, node, _ = _run(tmp_path, {"PIPESHUB_ROLE": "parsing", "USE_PARSING_SERVICE": "true"})
    assert modules == PARSE_PYTHON
    assert not node


def test_all_with_a_remote_parsing_url_runs_no_local_parsing_service(tmp_path: pathlib.Path) -> None:
    env = {"USE_PARSING_SERVICE": "true", "PARSING_SERVICE_URL": "http://rel-parsing:8092"}
    _, modules, node, _ = _run(tmp_path, env)
    assert modules == ALL_PYTHON | HARNESS_PYTHON | {"app.extraction_main"}
    assert node


def test_all_with_a_local_parsing_url_keeps_the_local_parsing_service(tmp_path: pathlib.Path) -> None:
    env = {"USE_PARSING_SERVICE": "true", "PARSING_SERVICE_URL": "http://localhost:8092"}
    _, modules, _, _ = _run(tmp_path, env)
    assert "app.parsing_main" in modules


@pytest.mark.parametrize("role", ["indexing", "PARSING", "x"])
def test_unknown_role_is_refused_and_starts_nothing(tmp_path: pathlib.Path, role: str) -> None:
    code, modules, node, out = _run(tmp_path, {"PIPESHUB_ROLE": role}, seconds=2)
    assert code == 1
    assert not modules and not node
    assert "unknown PIPESHUB_ROLE" in out


def test_parsing_role_listens_on_the_configured_port(tmp_path: pathlib.Path) -> None:
    _, _, _, out = _run(tmp_path, {"PIPESHUB_ROLE": "parsing", "PARSING_SERVICE_PORT": "9999"})
    assert "port 9999" in out


def test_parsing_role_drains_in_flight_parses_before_stopping_docling(tmp_path: pathlib.Path) -> None:
    """SIGTERM reaches the parsing process first; Docling stays up until it has exited."""
    app = tmp_path / "app"
    (app / "python").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    events = tmp_path / "events"
    python = bin_dir / "python"
    python.write_text(
        "#!/bin/bash\n"
        'case "$*" in\n'
        "  *app.capacity*) exit 0 ;;\n"
        f'  *parsing_main*) trap \'sleep 2; echo parsing-exit >> {events}; exit 0\' TERM ;;\n'
        f'  *) trap \'echo docling-term >> {events}; exit 0\' TERM ;;\n'
        "esac\n"
        "while true; do sleep 0.1; done\n"
    )
    python.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text("#!/bin/bash\nexit 0\n")
    curl.chmod(0o755)
    script = tmp_path / "process_monitor.sh"
    script.write_text(SCRIPT.read_text().replace("/app/", f"{app}/"))
    proc = subprocess.Popen(
        ["bash", str(script)],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "CHECK_INTERVAL": "30", "STARTUP_POLL_SECONDS": "1",
             "PIPESHUB_ROLE": "parsing"},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    time.sleep(3)
    proc.terminate()
    out, _ = proc.communicate(timeout=20)
    assert proc.returncode == 0, out
    assert events.read_text().split() == ["parsing-exit", "docling-term"], out


def _run_with_curl(tmp_path: pathlib.Path, curl_body: str, env: dict[str, str], seconds: float) -> tuple[int, set[str], str]:
    app = tmp_path / "app"
    (app / "python").mkdir(parents=True)
    (app / "backend").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    for name in ("python", "node"):
        stub = bin_dir / name
        stub.write_text(f'#!/bin/bash\ncase "$*" in *app.capacity*) exit 0 ;; esac\necho "{name} $*" >> {calls}\nexec sleep 60\n')
        stub.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text(f"#!/bin/bash\n{curl_body}\n")
    curl.chmod(0o755)
    script = tmp_path / "process_monitor.sh"
    script.write_text(SCRIPT.read_text().replace("/app/", f"{app}/"))
    log = tmp_path / "monitor.log"  # a file, not a pipe: a monitor that exits leaves its stub children holding a pipe open
    with log.open("w") as sink:
        proc = subprocess.Popen(
            ["bash", str(script)],
            env={"PATH": f"{bin_dir}:/usr/bin:/bin", "CHECK_INTERVAL": "1", "STARTUP_POLL_SECONDS": "1",
                 "STARTUP_STALL_SECONDS": "1", "STARTUP_MAX_SECONDS": "3", **env},
            stdout=sink, stderr=subprocess.STDOUT, text=True,
        )
        time.sleep(seconds)
        code = proc.poll()
        if code is None:
            proc.terminate()
            proc.wait(timeout=20)
    out = log.read_text()
    lines = calls.read_text().splitlines() if calls.exists() else []
    return (code if code is not None else -1), {ln.split("-m ", 1)[1].split()[0] for ln in lines if ln.startswith("python -m ")}, out


REMOTE = {"USE_PARSING_SERVICE": "true", "PARSING_SERVICE_URL": "http://rel-parsing:8092"}


def test_indexing_does_not_start_before_a_remote_parsing_service_is_ready(tmp_path: pathlib.Path) -> None:
    code, modules, out = _run_with_curl(
        tmp_path, 'case "$*" in *rel-parsing*) exit 22 ;; esac\nexit 0', REMOTE, seconds=8,
    )
    assert code == 1, out
    assert "app.indexing_main" not in modules
    assert "Remote Parsing service did not become healthy" in out


def test_indexing_starts_once_a_remote_parsing_service_is_ready(tmp_path: pathlib.Path) -> None:
    code, modules, out = _run_with_curl(
        tmp_path, f'echo "$*" >> {tmp_path}/curls\nexit 0', REMOTE, seconds=5,
    )
    assert code == -1, out
    assert "app.indexing_main" in modules
    assert "http://rel-parsing:8092/health/ready" in (tmp_path / "curls").read_text()
