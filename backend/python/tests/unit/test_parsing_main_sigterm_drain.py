"""SIGTERM on the parsing service lets an in-flight request finish before the process exits.

``parsing_main`` installs a ``sys.exit`` handler when it is imported; uvicorn replaces it with its own
while it serves, and drains in-flight requests on SIGTERM. A parse pod relies on that (with the process
monitor stopping Docling only after the parsing process has exited), so the property is pinned here.
"""
import os
import signal
import subprocess
import sys
import textwrap
import time
import urllib.request

SERVER = textwrap.dedent(
    """
    import asyncio, sys
    import uvicorn
    from fastapi import FastAPI

    from app.parsing_main import handle_sigterm  # noqa: F401 - importing it installs the module's handlers

    app = FastAPI()

    @app.get("/slow")
    async def slow():
        await asyncio.sleep(2)
        return {"done": True}

    @app.get("/up")
    async def up():
        return {}

    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
    """
)


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_an_in_flight_request_finishes_when_the_service_gets_sigterm(tmp_path) -> None:
    port = _free_port()
    script = tmp_path / "server.py"
    script.write_text(SERVER)
    proc = subprocess.Popen(
        [sys.executable, str(script), str(port)], cwd=os.getcwd(), env={**os.environ, "PYTHONPATH": os.getcwd()},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 90
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/up", timeout=2).read()
                break
            except OSError:
                assert proc.poll() is None and time.monotonic() < deadline, "the server did not start"
                time.sleep(0.5)
        import threading

        result: dict[str, object] = {}

        def call() -> None:
            try:
                result["body"] = urllib.request.urlopen(f"http://127.0.0.1:{port}/slow", timeout=20).read()
            except Exception as exc:  # noqa: BLE001
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
