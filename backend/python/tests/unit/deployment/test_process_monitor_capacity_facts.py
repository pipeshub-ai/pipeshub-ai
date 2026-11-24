"""The capacity facts export at the top of process_monitor.sh, and the service name each service starts with.

The block runs under ``set -e`` before any service starts. Whatever the helper prints, it must never stop
the monitor and never be evaluated; only a ``PIPESHUB_CAPACITY_FACTS=<json>`` line is exported.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[5]
SCRIPT = REPO / "deployment" / "process_monitor.sh"
PY_ROOT = REPO / "backend" / "python"
SHOW = 'echo "FACTS=${PIPESHUB_CAPACITY_FACTS:-unset}"'


def _block() -> str:
    m = re.search(r"^capacity_facts=\$\(.*?^unset capacity_facts[^\n]*\n", SCRIPT.read_text(), re.S | re.M)
    assert m, "the facts block is missing from process_monitor.sh"
    return m.group(0).replace("/app/python", str(PY_ROOT))


def _shim(tmp_path: pathlib.Path, body: str | None) -> str:
    shims = tmp_path / "bin"
    shims.mkdir(exist_ok=True)
    python = shims / "python"
    python.write_text("#!/bin/bash\n" + (body if body is not None else f'exec "{sys.executable}" -W ignore "$@"\n'))
    python.chmod(0o755)
    return f"{shims}:/usr/bin:/bin"


def _run(tmp_path: pathlib.Path, body: str | None, then: str = SHOW, block: str | None = None) -> subprocess.CompletedProcess[str]:
    script = "set -e\n" + (block if block is not None else _block()) + then + "\necho SURVIVED\n"
    # cwd is tmp_path, so a command a printed line runs (`touch PWN`) lands where the tests look for it.
    return subprocess.run(["bash", "-c", script], env={"PATH": _shim(tmp_path, body), "HOME": str(tmp_path)},
                          cwd=tmp_path, capture_output=True, text=True, timeout=60, check=False)


@pytest.mark.parametrize("body", ["exit 1\n", "echo boom >&2; exit 2\n", "kill -SEGV $$\n", "exit 0\n"])
def test_a_failing_export_is_not_fatal(tmp_path: pathlib.Path, body: str) -> None:
    out = _run(tmp_path, body)
    assert "SURVIVED" in out.stdout, out
    assert "FACTS=unset" in out.stdout


@pytest.mark.parametrize("noise", ["Some library banner", "WARNING: something", "$(touch PWN)", "export X=1; touch PWN", "`touch PWN`"])
def test_an_extra_stdout_line_is_neither_fatal_nor_run(tmp_path: pathlib.Path, noise: str) -> None:
    body = f"printf '%s\\n' {noise!r}\necho 'PIPESHUB_CAPACITY_FACTS={{\"v\":1}}'\n"
    out = _run(tmp_path, body, then=f"cd {tmp_path}; {SHOW}")
    assert "SURVIVED" in out.stdout, out
    assert 'FACTS={"v":1}' in out.stdout, "the valid line must still be exported"
    assert not (tmp_path / "PWN").exists(), "a printed line was executed"


@pytest.mark.parametrize("line", [
    "OTHER_KEY=1",
    "export PIPESHUB_CAPACITY_FACTS='{}'",  # the old shell form is not read
    "PIPESHUB_CAPACITY_FACTS=$(touch PWN)",
    "PIPESHUB_CAPACITY_FACTS=`touch PWN`",
    "PIPESHUB_CAPACITY_FACTS={};touch PWN",
    "PIPESHUB_CAPACITY_FACTS=",
    "PIPESHUB_CAPACITY_FACTS=" + "1" * 16385,
])
def test_only_the_listed_key_with_a_plain_value_is_exported(tmp_path: pathlib.Path, line: str) -> None:
    out = _run(tmp_path, f"printf '%s\\n' '{line}'\n", then=f"cd {tmp_path}; {SHOW}; echo \"OTHER=${{OTHER_KEY:-unset}}\"")
    assert "SURVIVED" in out.stdout, out
    assert "FACTS=unset" in out.stdout and "OTHER=unset" in out.stdout
    assert not (tmp_path / "PWN").exists()


def _a_printed_line_ran(tmp_path: pathlib.Path, block: str) -> bool:
    _run(tmp_path, "printf '%s\\n' 'touch PWN'\n", block=block)
    return (tmp_path / "PWN").exists()


def test_a_printed_line_is_never_run_by_the_block(tmp_path: pathlib.Path) -> None:
    assert not _a_printed_line_ran(tmp_path, _block())


def test_the_check_notices_a_block_that_runs_each_printed_line_in_a_subshell(tmp_path: pathlib.Path) -> None:
    """The check itself must be able to fail: a block that evaluates every line but still exports correctly."""
    loop = "while IFS= read -r capacity_line; do\n"
    block = _block()
    assert loop in block
    mutated = block.replace(loop, loop + '    ( eval "$capacity_line" ) >/dev/null 2>&1 || true\n')
    assert _a_printed_line_ran(tmp_path, mutated)


def test_the_real_export_reaches_a_child_and_is_accepted(tmp_path: pathlib.Path) -> None:
    child = (
        f'cd {PY_ROOT} && "{sys.executable}" -W ignore -c '
        '"from app.capacity.sources import Env; import sys; sys.exit(0 if Env().read() is not None else 7)" '
        "&& echo ACCEPTED"
    )
    out = _run(tmp_path, None, then=child)
    assert "ACCEPTED" in out.stdout and "SURVIVED" in out.stdout, out


def _spawn_functions() -> str:
    text = SCRIPT.read_text()
    parts = []
    for name in ("use_own_pg_login", "spawn_embedding", "start_docling", "start_indexing", "spawn_connector", "start_query",
                 "spawn_parsing", "spawn_extraction"):
        m = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.S | re.M)
        assert m, f"{name} not found in process_monitor.sh"
        parts.append(m.group(0))
    return "\n".join(parts)


def test_every_service_starts_with_its_own_capacity_service_name(tmp_path: pathlib.Path) -> None:
    """Without it a service logs no ``capacity:`` line and takes the first service's plan."""
    shims = tmp_path / "bin"
    shims.mkdir()
    python = shims / "python"
    python.write_text(f'#!/bin/bash\necho "${{2#app.}}=${{PIPESHUB_SERVICE:-MISSING}}" >> {tmp_path}/seen\n')
    python.chmod(0o755)
    script = f"""
log() {{ :; }}
cd() {{ :; }}
PARSING_PORT=1 EXTRACTION_PORT=2
{_spawn_functions()}
for f in spawn_embedding start_docling start_indexing spawn_connector start_query spawn_parsing spawn_extraction; do $f; done
wait
"""
    subprocess.run(["bash", "-c", script], env={"PATH": f"{shims}:/usr/bin:/bin"}, capture_output=True, text=True,
                   timeout=60, check=True)
    seen = dict(line.split("=", 1) for line in (tmp_path / "seen").read_text().split())
    assert seen == {
        "embedding_main": "embedding", "docling_main": "docling", "indexing_main": "indexing",
        "connectors_main": "connectors", "query_main": "query", "parsing_main": "parsing",
        "extraction_main": "extraction",
    }
