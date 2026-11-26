"""D-3 (no synthetic HTTP path), D-7 (slim worker), D-4 (the inline claim hint)."""

from __future__ import annotations

import importlib.util
import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.api.routes.harness import router
from app.harness.journal_node.credentials import (
    ConfiguredWorkerCredential,
    WorkerCredentialUnavailable,
)

# A hang guard, not a bound: the test imports the app in a subprocess, which a loaded host slows.
HEAVY_TIMEOUT_S = 180

PYTHON_ROOT = Path(__file__).resolve().parents[3]
HARNESS = PYTHON_ROOT / "app" / "harness"


def test_d3_the_synthetic_http_executor_is_gone() -> None:
    assert importlib.util.find_spec("app.harness.worker.agent_executor") is None
    assert importlib.util.find_spec("app.harness.worker.runner") is None
    offenders = [
        str(path.relative_to(PYTHON_ROOT))
        for path in [*HARNESS.rglob("*.py"), PYTHON_ROOT / "app" / "harness_main.py"]
        if any(
            marker in path.read_text(encoding="utf-8")
            for marker in ("starlette", "chat_stream(", "askAIStream", "SimpleNamespace(container")
        )
    ]
    assert offenders == []


_IMPORT_PROBE = """
import json, sys
import app.harness_main
heavy = ["app.containers.query", "app.containers.utils.utils", "app.modules.retrieval.retrieval_service",
         "app.agents.agent_loop.factory", "app.api.routes.agent", "langchain_core", "app.agent_loop_lib",
         "torch", "sentence_transformers", "qdrant_client"]
print(json.dumps({name: name in sys.modules for name in heavy}))
"""


def test_d7_the_idle_worker_imports_no_query_retrieval_or_llm_stack() -> None:
    out = subprocess.run(
        [sys.executable, "-c", _IMPORT_PROBE],
        cwd=PYTHON_ROOT,
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "", "SECRET_KEY": "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881", "PYTHONPATH": str(PYTHON_ROOT)},
    )
    loaded = json.loads(out.stdout.strip().splitlines()[-1])
    assert [name for name, present in loaded.items() if present] == []
    source = (PYTHON_ROOT / "app" / "harness_main.py").read_text(encoding="utf-8")
    assert "QueryAppContainer" not in source


class _Config:
    def __init__(self, document: object) -> None:
        self.document = document

    async def get_config(self, key: str, *args: object, **kwargs: object) -> object:
        return self.document


async def test_the_worker_credential_is_read_on_every_claim() -> None:
    config = _Config({"current": "phw_a"})
    credential = ConfiguredWorkerCredential(config)  # type: ignore[arg-type]
    assert await credential.worker_credential() == "phw_a"
    config.document = {"current": "phw_b"}
    assert await credential.worker_credential() == "phw_b"


@pytest.mark.parametrize("document", [None, {}, {"current": ""}, {"current": 5}, "phw_x"])
async def test_an_unprovisioned_credential_is_retryable(document: object) -> None:
    with pytest.raises(WorkerCredentialUnavailable):
        _ = await ConfiguredWorkerCredential(_Config(document)).worker_credential()  # type: ignore[arg-type]


async def test_the_hint_credential_is_current_or_previous_within_its_grace() -> None:
    now_ms = time.time() * 1000
    credential = ConfiguredWorkerCredential(
        _Config({"current": "phw_new", "previous": "phw_old", "previousValidUntil": now_ms + 60_000})  # type: ignore[arg-type]
    )
    assert await credential.accepts("phw_new")
    assert await credential.accepts("phw_old")
    assert not await credential.accepts("phw_other")
    assert not await credential.accepts(None)
    expired = ConfiguredWorkerCredential(
        _Config({"current": "phw_new", "previous": "phw_old", "previousValidUntil": now_ms - 1})  # type: ignore[arg-type]
    )
    assert not await expired.accepts("phw_old")


class _Inline:
    def __init__(self) -> None:
        self.credentials = ConfiguredWorkerCredential(_Config({"current": "phw_node"}))  # type: ignore[arg-type]
        self.hinted: list[tuple[str, str]] = []

    async def hint(self, org_id: str, run_id: str) -> bool:
        self.hinted.append((org_id, run_id))
        return True


def _app(inline: Any) -> FastAPI:  # noqa: ANN401
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/harness")
    app.state.harness_inline = inline
    return app


async def test_d4_the_inline_hint_takes_the_worker_credential_and_claims_in_process() -> None:
    inline = _Inline()
    transport = httpx.ASGITransport(app=_app(inline))
    async with httpx.AsyncClient(transport=transport, base_url="http://query") as client:
        refused = await client.post("/api/v1/harness/runs/run-1/claim", json={"orgId": "org-1"})
        wrong = await client.post(
            "/api/v1/harness/runs/run-1/claim",
            json={"orgId": "org-1"},
            headers={"Authorization": "Bearer phw_wrong"},
        )
        accepted = await client.post(
            "/api/v1/harness/runs/run-1/claim",
            json={"orgId": "org-1"},
            headers={"Authorization": "Bearer phw_node"},
        )
        bad_id = await client.post(
            "/api/v1/harness/runs/run%20one/claim",
            json={"orgId": "org-1"},
            headers={"Authorization": "Bearer phw_node"},
        )
    assert (refused.status_code, wrong.status_code) == (401, 401)
    assert (accepted.status_code, accepted.json()) == (202, {"claimed": True})
    assert bad_id.status_code == 422
    assert inline.hinted == [("org-1", "run-1")]


async def test_d4_the_inline_claimer_claims_the_hinted_run_by_id_with_the_inline_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.harness.inline as inline_module

    built: dict[str, Any] = {}

    class _Journal:
        def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401
            built.update(kwargs)
            self.claims: list[tuple[str | None, str | None]] = []

        async def claim(self, run_id: str | None = None, *, worker_id: str, org_id: str | None = None) -> None:
            self.claims.append((run_id, org_id))
            built["journal"] = self

        async def aclose(self) -> None:
            built["closed"] = True

    class _Container:
        def config_service(self) -> _Config:
            return _Config({"current": "phw_node"})

        def logger(self) -> Any:  # noqa: ANN401
            import logging

            return logging.getLogger("inline.tests")

    monkeypatch.setattr(inline_module, "NodeJournal", _Journal)
    inline = inline_module.InlineClaimer(_Container())
    assert await inline.hint("org-1", "run-7") is False
    assert built["role"] == "inline"
    assert built["journal"].claims[0] == ("run-7", "org-1")
    # E2E2 FB-2: the query service registers no engine kernel, so it must never be handed an engine run.
    assert "executor_engine" not in {f.root for f in built["capabilities"].features}
    await inline.aclose()
    assert built["closed"] is True


_WARM_PROBE = """
import ast, asyncio, json, pathlib, sys
from app.harness.worker.warmup import warm_up
from app.agents.registry.toolset_registry import get_toolset_registry
from harness_core.logs import HarnessLog
assert asyncio.run(warm_up(HarnessLog.named("probe")))
# The assembly discovers the toolsets by module name (importlib), which no import statement below shows (OPS1).
assert get_toolset_registry().get_all_toolsets(), "the toolset registry is still undiscovered"
lazy = set()
for path in pathlib.Path("app/harness/assembly").glob("*.py"):
    for fn in ast.walk(ast.parse(path.read_text())):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lazy.update(n.module for n in ast.walk(fn) if isinstance(n, ast.ImportFrom) and n.module and n.level == 0)
print(json.dumps(sorted(m for m in lazy if m.startswith("app.") and not m.startswith("app.harness.") and m not in sys.modules)))
"""


@pytest.mark.timeout(HEAVY_TIMEOUT_S)
def test_c17_the_warm_up_imports_everything_the_assembly_imports_on_first_use() -> None:
    out = subprocess.run(
        [sys.executable, "-c", _WARM_PROBE],
        cwd=PYTHON_ROOT,
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "", "SECRET_KEY": "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881", "PYTHONPATH": str(PYTHON_ROOT)},
    )
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []


# A provider that answers 429 at once: the first model call of a warmed worker, end to end through the SDK.
_FIRST_CALL_PROBE = """
import asyncio, json, sys, threading, http.server
from app.harness.worker.warmup import ASSEMBLY_MODULES, _import_all
_import_all(ASSEMBLY_MODULES)
from harness_core.kernel.core.messages import UserMessage
from harness_core.kernel.durable.adapters import StreamingModelPort
from harness_core.kernel.durable.ports import ModelCall
from harness_core.models.transport_model import TransportModel
from harness_core.models.transports.openai import OpenAITransport
from app.harness.assembly.model import ClassifiedModelPort

class Limited(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", 0)))
        self.send_response(429)
        self.send_header("Retry-After", "7")
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":{"message":"slow down","type":"rate_limit"}}')
    def log_message(self, *args): pass

server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Limited)
threading.Thread(target=server.serve_forever, daemon=True).start()
transport = OpenAITransport(api_key="k", model="m", base_url=f"http://127.0.0.1:{server.server_address[1]}/v1", max_retries=0)
port = ClassifiedModelPort(StreamingModelPort(TransportModel(transport), provider="openAI", transport="direct"))

async def first_call():
    async def on_delta(kind, text): pass
    before = set(sys.modules)
    try:
        await port.sample(ModelCall(messages=[UserMessage(content="hi")]), on_delta)
    except Exception as exc:
        outcome = type(exc).__name__
    return outcome, sorted(set(sys.modules) - before)

outcome, new = asyncio.run(first_call())
print(json.dumps({"outcome": outcome, "imported": new}))
"""


@pytest.mark.timeout(HEAVY_TIMEOUT_S)
def test_c17_a_warmed_workers_first_model_call_imports_nothing() -> None:
    """Whatever the first call imports runs on the event loop and holds the run's lease renewals (FLK7-2)."""
    out = subprocess.run(
        [sys.executable, "-c", _FIRST_CALL_PROBE],
        cwd=PYTHON_ROOT,
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "", "SECRET_KEY": "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881", "PYTHONPATH": str(PYTHON_ROOT)},
    )
    first_call = json.loads(out.stdout.strip().splitlines()[-1])
    assert first_call == {"outcome": "ModelBackoff", "imported": []}


def _events(caplog: pytest.LogCaptureFixture, event: str) -> list[dict[str, Any]]:
    return [r.harness for r in caplog.records if getattr(r, "harness_event", None) == event]  # type: ignore[attr-defined]


async def test_c17_the_warm_up_logs_what_it_took(caplog: pytest.LogCaptureFixture) -> None:
    from app.harness.worker.warmup import warm_up
    from harness_core.logs import HarnessLog

    caplog.set_level(logging.INFO)
    assert await warm_up(HarnessLog.named("test.warmup"), modules=["json"]) is True
    (warmed,) = _events(caplog, "worker.warmed")
    assert warmed["modules"] == 1 and warmed["ms"] >= 0


async def test_ops1_5_a_slow_warm_up_never_holds_the_event_loop() -> None:
    """MCP-FIX §8: a first tool use that discovered the registry on the loop held it past a lease renewal."""
    import asyncio
    import threading

    from app.harness.worker.warmup import warm_up
    from harness_core.logs import HarnessLog

    release = threading.Event()
    ticks = 0

    def slow_discovery() -> None:
        release.wait(5)

    async def renewals() -> None:
        nonlocal ticks
        while ticks < 3:
            await asyncio.sleep(0.01)
            ticks += 1
        release.set()

    warming = asyncio.create_task(warm_up(HarnessLog.named("test.warmup"), modules=["json"], discover=slow_discovery))
    await asyncio.wait_for(renewals(), 2)
    assert await warming is True
    assert ticks == 3


async def test_c17_a_failed_warm_up_is_logged_and_the_worker_still_starts(caplog: pytest.LogCaptureFixture) -> None:
    from app.harness.worker.warmup import warm_up
    from harness_core.logs import HarnessLog

    caplog.set_level(logging.INFO)
    assert await warm_up(HarnessLog.named("test.warmup"), modules=["app.harness.no_such_module"]) is False
    (failed,) = _events(caplog, "worker.warm_up_failed")
    assert failed["errorType"] == "ModuleNotFoundError"
    assert _events(caplog, "worker.warmed") == []
