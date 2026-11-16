"""SIGTERM on the parsing service lets an in-flight request finish before the process exits.

The pod runs ``python -m app.parsing_main``, which calls ``uvicorn.run("app.parsing_main:app")``. uvicorn
installs its own SIGTERM handler while it serves, then imports the app string, so any handler the module
sets at import replaces it and drops the requests in flight. Two pins: the module sets none, and a service
started that way (module run as ``__main__``, app given as an import string) drains.
"""
import ast
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
import urllib.request
from pathlib import Path

PARSING_MAIN = Path(__file__).resolve().parents[2] / "app" / "parsing_main.py"

SERVICE = textwrap.dedent(
    """
    import asyncio, sys
    import uvicorn
    from fastapi import FastAPI

    app = FastAPI()

    @app.get("/slow")
    async def slow():
        await asyncio.sleep(2)
        return {"done": True}

    @app.get("/up")
    async def up():
        return {}

    if __name__ == "__main__":
        # As app/parsing_main.py does: an import string, so the module is imported again inside uvicorn.
        uvicorn.run("drain_service:app", host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
    """
)


def test_the_module_installs_no_signal_handler_of_its_own() -> None:
    tree = ast.parse(PARSING_MAIN.read_text())
    top_level_calls = [
        ast.unparse(node.value.func) for node in tree.body if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
    ]
    assert "signal.signal" not in top_level_calls
    assert not any(isinstance(node, ast.FunctionDef) and node.name == "handle_sigterm" for node in tree.body)


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_a_service_started_the_way_the_pod_starts_it_drains_on_sigterm(tmp_path: Path) -> None:
    port = _free_port()
    (tmp_path / "drain_service.py").write_text(SERVICE)
    proc = subprocess.Popen(
        [sys.executable, "-m", "drain_service", str(port)], cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(tmp_path)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/up", timeout=2).read()
                break
            except OSError:
                assert proc.poll() is None and time.monotonic() < deadline, "the service did not start"
                time.sleep(0.3)
        result: dict[str, object] = {}

        def call() -> None:
            try:
                result["body"] = urllib.request.urlopen(f"http://127.0.0.1:{port}/slow", timeout=20).read()
            except Exception as exc:
                result["error"] = exc

        thread = threading.Thread(target=call)
        thread.start()
        time.sleep(0.5)
        proc.send_signal(signal.SIGTERM)
        thread.join(timeout=20)
        assert "error" not in result, result
        assert b"done" in result["body"]
        assert proc.wait(timeout=20) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
