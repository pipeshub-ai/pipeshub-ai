"""QA round 3 probes for the persistent SVG renderer: deadline, environment, fork, exit, replies."""
from __future__ import annotations

import base64
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.modules.parsers.image_parser import svg_worker

NS = 'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
GOOD = f'<svg {NS} width="20" height="20"><rect width="20" height="20" fill="red"/></svg>'
PYTHON_ROOT = str(Path(__file__).resolve().parents[4])


def _b64(svg: str) -> str:
    return base64.b64encode(svg.encode()).decode()


def _hostile() -> str:
    return (
        f'<svg {NS} width="200" height="200"><defs><symbol id="s"><circle r="2"/></symbol></defs>'
        + "".join(f'<use href="#s" x="{i % 190}" y="{i % 97}" width="4" height="4"/>' for i in range(15000))
        + "</svg>"
    )


def _fresh_pool(monkeypatch: pytest.MonkeyPatch) -> svg_worker._Renderers:
    pool = svg_worker._Renderers()
    monkeypatch.setattr(svg_worker, "_renderers", pool)
    return pool


def _fake_child(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str) -> None:
    (tmp_path / "fake_renderer.py").write_text(textwrap.dedent(body))
    monkeypatch.setattr(svg_worker, "_MODULE", "fake_renderer")
    monkeypatch.setattr(svg_worker, "_PYTHON_ROOT", str(tmp_path))


def _state(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:
        return "gone"


def test_the_deadline_also_holds_for_a_renderer_that_has_just_started(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fresh_pool(monkeypatch)
    _fake_child(monkeypatch, tmp_path, "import time\ntime.sleep(60)\n")
    start = time.monotonic()
    with pytest.raises(DocumentProcessingError):
        svg_worker.run_bounded(_b64(GOOD), timeout=1.0)
    assert time.monotonic() - start < 3.0  # the first SVG of a renderer gets 1 s + 5 s today


def test_the_renderer_does_not_inherit_the_service_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _fresh_pool(monkeypatch)
    monkeypatch.setenv("QA_SECRET_TOKEN", "hunter2")
    monkeypatch.setenv("MONGO_URI", "mongodb://user:pw@host")
    svg_worker.run_bounded(_b64(GOOD))
    renderer = pool._idle[0]
    environ = Path(f"/proc/{renderer.process.pid}/environ").read_bytes()
    pool.close_all()
    assert b"hunter2" not in environ
    assert b"MONGO_URI" not in environ


@pytest.mark.skipif(not hasattr(os, "fork"), reason="posix only")
def test_a_forked_child_does_not_kill_the_renderers_of_its_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _fresh_pool(monkeypatch)
    svg_worker.run_bounded(_b64(GOOD))
    pid = pool._idle[0].process.pid
    child = os.fork()
    if child == 0:  # pragma: no cover - runs in the forked process
        try:
            svg_worker.run_bounded(_b64(GOOD))
        finally:
            os._exit(0)
    os.waitpid(child, 0)
    try:
        assert _state(pid) not in ("Z", "gone"), "the forked child killed the parent's renderer"
    finally:
        pool.close_all()


def test_a_renderer_in_the_middle_of_an_svg_does_not_outlive_its_parent(tmp_path: Path) -> None:
    script = tmp_path / "parent.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import sys, threading, time
            sys.path.insert(0, {PYTHON_ROOT!r})
            from app.modules.parsers.image_parser import svg_worker
            svg = {_b64(_hostile())!r}
            def go():
                try:
                    svg_worker.run_bounded(svg)
                except Exception:
                    pass  # the renderer is closed when this process exits
            threading.Thread(target=go, daemon=True).start()
            time.sleep(2)
            import glob, os
            kids = [p for f in glob.glob(f"/proc/{{os.getpid()}}/task/*/children") for p in open(f).read().split()]
            print(kids[0], flush=True)
            """
        )
    )
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60, check=True)
    pid = int(out.stdout.split()[-1])
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and _state(pid) not in ("gone", "Z"):
            time.sleep(0.1)
        assert _state(pid) in ("gone", "Z"), "the renderer kept rasterising after its parent exited"
    finally:
        with __import__("contextlib").suppress(OSError):
            os.kill(pid, signal.SIGKILL)


def test_a_reply_that_is_not_json_is_a_processing_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pool = _fresh_pool(monkeypatch)
    _fake_child(
        monkeypatch,
        tmp_path,
        "import sys\nfor line in sys.stdin:\n    sys.stdout.write('not json\\n'); sys.stdout.flush()\n",
    )
    try:
        with pytest.raises(DocumentProcessingError):
            svg_worker.run_bounded(_b64(GOOD), timeout=3.0)
    finally:
        pool.close_all()


@pytest.mark.skipif(not hasattr(signal, "SIGSTOP"), reason="posix only")
def test_a_stuck_renderer_cannot_block_the_caller_past_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _fresh_pool(monkeypatch)
    svg_worker.run_bounded(_b64(GOOD))
    renderer = pool._idle[0]
    os.kill(renderer.process.pid, signal.SIGSTOP)
    big = _b64(GOOD.replace("<rect", "<!--" + "a" * (3 * 1024 * 1024) + "--><rect"))
    outcome: list[object] = []

    def call() -> None:
        try:
            svg_worker.run_bounded(big, timeout=2.0)
            outcome.append("ok")
        except DocumentProcessingError as exc:
            outcome.append(exc)

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    thread.join(timeout=8)
    stuck = thread.is_alive()
    # The renderer is killed at the deadline; with the old blocking write it was still
    # stopped here and this let the blocked write finish so the test could clean up.
    with __import__("contextlib").suppress(ProcessLookupError):
        os.kill(renderer.process.pid, signal.SIGCONT)
    thread.join(timeout=10)
    pool.close_all()
    assert not stuck, "the request write has no deadline: the caller blocks while the renderer is stopped"


# --- round 4 ---------------------------------------------------------------------


def test_the_renderer_does_not_inherit_secrets_whose_names_look_like_locale_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R4-D1: the allowlist takes every ``LC_*`` name, so ``LC_<anything>`` reaches the renderer."""
    pool = _fresh_pool(monkeypatch)
    monkeypatch.setenv("LC_AWS_SECRET_ACCESS_KEY", "qa-lc-secret")
    monkeypatch.setenv("LC_API_TOKEN", "qa-lc-token")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")  # a real locale variable may pass
    svg_worker.run_bounded(_b64(GOOD))
    environ = Path(f"/proc/{pool._idle[0].process.pid}/environ").read_bytes()
    pool.close_all()
    assert b"qa-lc-secret" not in environ
    assert b"qa-lc-token" not in environ


@pytest.mark.parametrize(
    "reply",
    ["[" * 200000, '{"png": ' + "[" * 100000, '{"png": ' + "{\"a\":" * 100000],
    ids=["deep-list", "deep-png", "deep-object"],
)
def test_a_reply_nested_too_deeply_is_a_processing_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reply: str
) -> None:
    """R4-D2: ``json.loads`` raises RecursionError (not ValueError) on deep nesting, which escapes raw."""
    pool = _fresh_pool(monkeypatch)
    _fake_child(
        monkeypatch,
        tmp_path,
        f"import sys\nfor line in sys.stdin:\n    sys.stdout.write({reply!r} + '\\n'); sys.stdout.flush()\n",
    )
    used: list[svg_worker._Renderer] = []
    acquire = pool.acquire
    monkeypatch.setattr(pool, "acquire", lambda deadline: used.append(acquire(deadline)) or used[-1])
    try:
        with pytest.raises(DocumentProcessingError):
            svg_worker.run_bounded(_b64(GOOD), timeout=3.0)
        assert not used[0].alive()  # the renderer that sent it is not reused
    finally:
        pool.close_all()



def test_a_renderer_writes_no_core_file() -> None:
    """It parses hostile input; a crash must not write that memory to disk."""
    import subprocess
    import sys

    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "import resource, sys\n"
            "import app.modules.parsers.image_parser.svg_worker as w\n"
            "sys.stdin = open('/dev/null')\n"
            "w.main()\n"
            "print(resource.getrlimit(resource.RLIMIT_CORE), file=sys.stderr)\n",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "(0, 0)" in child.stderr, child.stderr[-500:]
