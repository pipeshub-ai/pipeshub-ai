"""R46LANE-FU-3: a drained worker stop closes every aiohttp session the worker opened.

On lane ``l46a`` the worker logged ``Unclosed client session`` / ``Unclosed connector`` after ``worker.stopped``:
the engine plan's sandbox broker client, the process's own broker client (MCP runner, run_code), the egress proxy's
control-plane client and the pooled blob-storage session were never closed. These drive ``run_worker`` from start to
a SIGTERM with every outside service stubbed (no Docker, no network) and track each ``ClientSession`` it creates.
"""

from __future__ import annotations

import asyncio
import os
import signal
from typing import TYPE_CHECKING, Any

import aiohttp
import pytest

from app import harness_main
from app.harness.engines import durable as durable_module
from app.harness.engines import worker as engine_worker_module
from app.harness.engines.worker import EnginePlan
from app.harness.repositories import EgressControl
from app.harness.sandbox import client as sandbox_client
from app.modules.transformers.blob_storage import get_shared_session
from harness_core.sandbox.failover import FailoverSandboxBroker, SandboxProvider
from harness_core.sandbox.provider import BrokerSandboxProvider, SandboxProviderKind
from harness_core.sandbox.remote.client import RemoteSandboxBroker

if TYPE_CHECKING:
    from collections.abc import Iterator


class _Auth:
    async def headers(self) -> dict[str, str]:
        return {}


def _opened_provider(url: str) -> BrokerSandboxProvider[FailoverSandboxBroker]:
    """A Docker provider over a primary and a fallback broker client, each holding its session as ``open`` leaves it
    (``open`` asks the broker for its modes)."""
    brokers = [RemoteSandboxBroker(f"{url}/{name}", _Auth()) for name in ("primary", "fallback")]
    for broker in brokers:
        _ = broker._http()  # pyright: ignore[reportPrivateUsage]
    return BrokerSandboxProvider(
        FailoverSandboxBroker([SandboxProvider(name, b) for name, b in zip(("primary", "fallback"), brokers)]),
        SandboxProviderKind.DOCKER,
    )


class _Closable:
    async def aclose(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def start(self, *_: object, **__: object) -> None:
        return None


class _Journal(_Closable):
    def __init__(self, **_: object) -> None:
        pass

    def attempt_route(self, *_: object) -> Any:  # noqa: ANN401
        raise AssertionError("no run is claimed")


class _Container:
    def logger(self) -> Any:  # noqa: ANN401
        import logging

        return logging.getLogger("r46lane-fu3")

    def config_service(self) -> Any:  # noqa: ANN401
        return object()

    def blob_store(self) -> object:
        return object()

    def graph_provider(self) -> object:
        return object()

    def shutdown_resources(self) -> None:
        return None


class _Consumer(_Closable):
    async def start(self, *_: object, **__: object) -> None:
        await asyncio.Event().wait()


class _Claimer(_Closable):
    """Stands in for the runs: what they open while the worker serves, then the operator's SIGTERM."""

    opened: list[aiohttp.ClientSession]
    assemblers: list[Any]

    def __init__(self, **_: object) -> None:
        pass

    def start(self) -> None:
        loop = asyncio.get_running_loop()
        loop.create_task(self._serve())

    async def _serve(self) -> None:
        # A run's sandbox code or MCP server takes the process's broker client; a run reads its inputs from storage.
        _ = await sandbox_client.process_broker_client.get()
        _ = get_shared_session()
        # An engine run's site grant or volume charge goes through the egress proxy's control plane.
        for assembler in _Claimer.assemblers:
            control = getattr(assembler, "_egress_control", None) or EgressControl(object())
            _ = control._control()._http()  # pyright: ignore[reportPrivateUsage]
        os.kill(os.getpid(), signal.SIGTERM)


class _Assembler(durable_module.PipesHubEngineAssembler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        super().__init__(*args, **kwargs)
        _Claimer.assemblers.append(self)


class _Driver:
    def __init__(self, **_: object) -> None:
        pass


async def _none(*_: object, **__: object) -> None:
    return None


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[aiohttp.ClientSession]]:
    created: list[aiohttp.ClientSession] = []
    init = aiohttp.ClientSession.__init__

    def tracking(self: aiohttp.ClientSession, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        init(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(aiohttp.ClientSession, "__init__", tracking)
    yield created
    # A failing run's leftovers must not warn into later tests: their loop is gone, so they are detached, not closed.
    for session in created:
        if not session.closed:
            session.detach()


@pytest.fixture
def stubbed_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """``run_worker``'s outside services stubbed; the engine worker, its assembler and the clients are real."""
    container = _Container()
    _Claimer.assemblers = []
    monkeypatch.setenv("HARNESS_TOOL_GATEWAY_URL", "http://worker.invalid/harness/tool-gateway")
    monkeypatch.setattr(harness_main, "validate_env_flags", lambda: None)
    monkeypatch.setattr(harness_main, "mark_process_non_dumpable", lambda: None)
    monkeypatch.setattr(harness_main, "preload_in_background", lambda: None)
    monkeypatch.setattr(harness_main, "HarnessServices", lambda: container)
    monkeypatch.setattr("app.harness.assembly.services.HarnessServices", lambda: container)
    monkeypatch.setattr(harness_main, "configure_envelope", _none)

    async def plan(_services: object) -> EnginePlan:
        return EnginePlan(_opened_provider("http://broker.invalid"), (), 0)

    async def open_provider(_config: object) -> BrokerSandboxProvider[FailoverSandboxBroker]:
        return _opened_provider("http://process-broker.invalid")

    monkeypatch.setattr(harness_main, "plan_engines", plan)
    monkeypatch.setattr(sandbox_client, "open_sandbox_provider", open_provider)
    monkeypatch.setattr(sandbox_client.process_broker_client, "_client", None)

    class _LiveBus:
        @staticmethod
        async def create(_config: object) -> None:
            return None

    monkeypatch.setattr(harness_main, "RedisLiveBus", _LiveBus)
    monkeypatch.setattr(harness_main, "ConfiguredWorkerCredential", lambda _config: object())
    monkeypatch.setattr(harness_main, "NodeJournal", _Journal)
    monkeypatch.setattr(harness_main, "worker_live_layer", lambda *_a, **_k: _Closable())
    monkeypatch.setattr(harness_main, "RunPolicies", lambda *_a, **_k: object())

    async def admission(*_: object) -> None:
        return None

    monkeypatch.setattr(harness_main, "provider_admission", admission)

    async def blob_store(self: object) -> object:
        return object()

    monkeypatch.setattr(harness_main.ContainerServices, "blob_store", blob_store)
    monkeypatch.setattr(harness_main, "PipesHubBinaryObjectStore", lambda _store: object())
    monkeypatch.setattr(engine_worker_module, "PipesHubEngineAssembler", _Assembler)
    monkeypatch.setattr(harness_main, "PipesHubRuntimeAssembler", lambda *_a, **_k: object())
    monkeypatch.setattr(harness_main, "run_slots", lambda: 1)
    monkeypatch.setattr(harness_main, "RunClaimer", _Claimer)

    async def consumer_config(*_: object, **__: object) -> object:
        return object()

    monkeypatch.setattr(harness_main.MessagingUtils, "create_consumer_config", consumer_config)
    monkeypatch.setattr(harness_main.MessagingFactory, "create_consumer", lambda **_: _Consumer())
    monkeypatch.setattr(harness_main, "RevocationRefresher", lambda *_a, **_k: _Closable())
    monkeypatch.setattr(harness_main, "MetricsPusher", lambda *_a, **_k: _Closable())
    monkeypatch.setattr(harness_main, "freeze_startup_heap", lambda _log: None)
    monkeypatch.setattr("app.harness.repositories.publish_cache_status", _none)
    monkeypatch.setattr(harness_main, "references_job", lambda *_a: None)
    monkeypatch.setattr(harness_main, "log_capabilities", lambda *_a: None)
    monkeypatch.setattr(harness_main.process_mcp_hub, "aclose", _none)
    monkeypatch.setattr(harness_main.process_mcp_runner, "aclose", _none)


@pytest.mark.usefixtures("stubbed_worker")
def test_r46lane_fu3_a_drained_stop_leaves_no_client_session_open(sessions: list[aiohttp.ClientSession]) -> None:
    async def serve_until_sigterm() -> None:
        await asyncio.wait_for(harness_main.run_worker(driver_type=_Driver, capabilities=object()), 30)  # pyright: ignore[reportArgumentType]

    asyncio.run(serve_until_sigterm())

    # Two plan brokers, two process brokers, the blob pool and the egress proxy's control client.
    assert len(sessions) == 6, f"the stand-in runs opened {len(sessions)} sessions"
    left_open = [s for s in sessions if not s.closed]
    assert not left_open, f"{len(left_open)} of {len(sessions)} aiohttp sessions left open after worker.stopped"


def test_r46lane_fu3_runs_share_one_egress_control_client_closed_with_the_assembler() -> None:
    """A client per run left a connection pool open for each; the assembler's one client closes with it."""

    class _Services:
        def config_service(self) -> object:
            return object()

    async def scenario() -> None:
        assembler = durable_module.PipesHubEngineAssembler(_Services(), object())  # pyright: ignore[reportArgumentType]
        session = assembler._egress_control._control()._http()  # pyright: ignore[reportPrivateUsage]
        await assembler.aclose()
        assert session.closed

    asyncio.run(scenario())
