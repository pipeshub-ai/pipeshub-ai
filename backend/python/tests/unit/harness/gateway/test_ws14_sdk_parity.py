"""SP-1..SP-4 (WS-14, C3.16): a stub call and ``ph tools call`` return identical results, errors included.

As PC-2 (``test_ph_contract``) for ``ph`` against the MCP client, each case runs the same calls three times, each
against its own identical run behind the real gateway transport (``ToolGatewayApp`` over HTTP): once with
``ph --json tools call``, once with the generated Python stub and once with the generated TypeScript stub, every
call its own process reading the turn's session folder as a sandbox does. The three must give the same answer
(text, or error code, exit code and message), and their runs the same decisions, executions, holds for a person
and effect log entries (the journal's source). The cases are PC-2's (allowed, asked then approved, asked and
undecided, asked then denied, policy denial, ungranted, a credential in the arguments, a tool error and an unknown
outcome, through ``call_tool``, the flow rule's ask) and three of transport: a call that outlives ``ph``'s
timeout, credentials the gateway refuses, and a gateway that is down.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import socket
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.engines.controller import EngineControllerConfig
from harness_core.engines.tool_folder import granted_tools
from harness_core.testing.contracts.tool_endpoint import BINDING, ScriptedHold
from harness_engines.pipeshub import sdk as sdk_module
from harness_engines.pipeshub.sdk import stubs
from tests.unit.harness.gateway.support import GatewayServer
from tests.unit.harness.gateway.test_ph_contract import (  # pyright: ignore[reportPrivateUsage]
    CASES,
    _world,
)
from tests.unit.harness_engines.pipeshub.sdk_support import (
    lay_out,
    node,
    through_ph,
    through_python,
    through_typescript,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping
    from pathlib import Path

ENDPOINTS = EngineControllerConfig(images={}).endpoints("pxh-model-aaaa", "pxh-tools-bbbb")
# Which stub error each failure raises, in both languages: the class a script catches.
KINDS = {
    "tool_error": "ToolFailed",
    "approval_pending": "ApprovalPending",
    "not_found": "NotFound",
    "unavailable": "Unavailable",
    "not_permitted": "NotPermitted",
}


@pytest.fixture
async def server() -> AsyncIterator[GatewayServer]:
    async with GatewayServer().running() as running:
        yield running


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.asynccontextmanager
async def _session(server: GatewayServer, world: Any, root: Path, token: str | None = None, url: str | None = None) -> AsyncIterator[Path]:  # noqa: ANN401
    """The run served by the gateway and its session folder laid out under ``root``, naming the run's URL and a
    token for its binding (the egress proxy's swap is outside this test: ``ph`` and the stubs send the same)."""
    async with server.gateway.serving(world.endpoint, world.endpoint.binding) as key:
        endpoints = dataclasses.replace(
            ENDPOINTS,
            tools_mcp_url=url or server.url(key),
            tools_token_placeholder=token or server.mint.mint(BINDING),
        )
        yield lay_out(root, granted_tools(world.endpoint), endpoints)


async def _through(way: str, folder: Path, world: Any, name: str, arguments: Mapping[str, Any], extra: Mapping[str, str]) -> tuple[dict[str, Any], str | None]:  # noqa: ANN401
    tools = granted_tools(world.endpoint)
    if way == "ph":
        envelope, code = await through_ph(folder, name, arguments, extra)
        assert code == (0 if envelope["ok"] else envelope["error"]["exit"])
        return envelope, None
    if way == "python":
        return await through_python(folder, tools, name, arguments, extra)
    return await through_typescript(folder, tools, name, arguments, extra)


def _ways() -> list[str]:
    return ["ph", "python"] + (["typescript"] if node() is not None else [])


async def _run_case(
    server: GatewayServer, tmp_path: Path, way: str, options: dict[str, Any], calls: list[tuple[str, dict[str, Any]]],
    *, extra: Mapping[str, str] = {}, token: str | None = None, url: str | None = None,
) -> tuple[list[tuple[dict[str, Any], str | None]], list[tuple[str, ...]], list[str], int]:
    world, log, hold = _world(options)
    answers = []
    async with _session(server, world, tmp_path / way, token, url) as folder:
        for name, arguments in calls:
            answers.append(await _through(way, folder, world, name, arguments, extra))
    return answers, log.entries, world.runs, hold.calls if hold is not None else 0


@pytest.mark.parametrize(("case", "options", "calls"), CASES, ids=[c[0] for c in CASES])
async def test_sp1_a_stub_call_and_ph_tools_call_get_the_same_answer_decision_and_journal(
    server: GatewayServer, tmp_path: Path, case: str, options: dict[str, Any], calls: list[tuple[str, dict[str, Any]]]
) -> None:
    """SP-1 (C3.16): the same answer, errors included, the same decisions and the same journal, three ways."""
    seen = {way: await _run_case(server, tmp_path, way, options, calls) for way in _ways()}
    answers, entries, runs, holds = seen["ph"]
    for way, (stub_answers, stub_entries, stub_runs, stub_holds) in seen.items():
        assert [a for a, _ in stub_answers] == [a for a, _ in answers], way
        assert (stub_entries, stub_runs, stub_holds) == (entries, runs, holds), way
        for answer, kind in stub_answers:
            if way != "ph" and not answer["ok"]:
                assert kind == KINDS[answer["error"]["code"]], (way, answer)
    assert len(answers) == len(calls)
    if case in ("allowed-read", "through-call-tool"):
        assert all(a["ok"] for a, _ in answers) and runs == [c[0] for c in calls]
    if case in ("policy-deny", "ungranted", "credential-in-arguments", "asked-then-denied"):
        assert runs == [] and answers[0][0]["error"]["code"] in ("tool_error", "not_found")


async def test_sp2_a_stub_call_never_skips_an_ask(server: GatewayServer, tmp_path: Path) -> None:
    """SP-2 (C3.16): a write the run's policy asks about is held for a person through a stub exactly as through
    ``ph``: the tool does not run, the call answers ``approval_pending`` (exit 75, ``ApprovalPending``) and the
    hold is asked once per call."""
    options = {"approvals": {"transfer": "ask"}, "hold_factory": lambda: ScriptedHold(None), "hold_s": 0.2}
    for way in _ways():
        [(answer, kind)], _, runs, holds = await _run_case(server, tmp_path, way, options, [("transfer", {"q": "1"})])
        assert answer["ok"] is False and answer["error"]["code"] == "approval_pending" and answer["error"]["exit"] == 75, way
        assert kind in (None, "ApprovalPending") and runs == [] and holds == 1, way


@pytest.mark.parametrize("failure", ["timeout", "credentials-refused", "gateway-down"])
async def test_sp3_transport_failures_answer_alike(server: GatewayServer, tmp_path: Path, failure: str) -> None:
    """SP-3 (C3.16): a call that outlives ``ph``'s timeout (``unavailable``), credentials the gateway refuses
    (``not_permitted``) and a gateway that is down (``unavailable``) answer the same through every way."""
    options: dict[str, Any] = {}
    kwargs: dict[str, Any] = {}
    async def silent(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(65536)
        await asyncio.sleep(3)
        writer.close()

    async with contextlib.AsyncExitStack() as stack:
        if failure == "timeout":
            # A gateway that takes the request and never answers: ph gives up after PH_TIMEOUT_S.
            listener = await asyncio.start_server(silent, "127.0.0.1", 0)
            stack.push_async_callback(listener.wait_closed)
            stack.callback(listener.close)
            port = listener.sockets[0].getsockname()[1]
            kwargs["url"] = f"http://127.0.0.1:{port}/harness/tool-gateway/x/mcp"
            kwargs["extra"] = {"PH_TIMEOUT_S": "1"}
        elif failure == "credentials-refused":
            kwargs["token"] = "pxh-tools-not-a-token"
        else:
            kwargs["url"] = f"http://127.0.0.1:{_closed_port()}/harness/tool-gateway/x/mcp"
        seen = {way: (await _run_case(server, tmp_path, way, options, [("transfer", {"q": "1"})], **kwargs))[0] for way in _ways()}
    (expected, _), = seen["ph"]
    code = {"timeout": "unavailable", "credentials-refused": "not_permitted", "gateway-down": "unavailable"}[failure]
    assert expected["ok"] is False and expected["error"]["code"] == code, expected
    for way, [(answer, kind)] in seen.items():
        assert answer == expected, way
        assert kind in (None, KINDS[code]), way


async def test_sp4_every_granted_tool_has_a_stub_and_only_those(server: GatewayServer) -> None:
    """SP-4 (C3.15/C3.16): the parity cases call the generated function for each granted tool (never the generic
    ``call``), and the one ungranted tool has no stub: only ``call`` reaches it, and the gateway refuses it."""
    world, _, _ = _world({})
    granted = granted_tools(world.endpoint)
    names = {s.tool for _, rendered in stubs(granted).values() for s in rendered}
    assert names == {t.name for t in granted}
    called = {name for _, _, calls in CASES for name, _ in calls}
    assert called - names == {"delete"}


@pytest.mark.parametrize(("case", "options", "calls"), CASES, ids=[c[0] for c in CASES])
async def test_sp5_c5_01_stubs_built_on_first_import_answer_as_ph(
    server: GatewayServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, options: dict[str, Any],
    calls: list[tuple[str, dict[str, Any]]],
) -> None:
    """SP-5 (C5.01): with every grant past the eager threshold, the stubs rendered on first import (Python) and the
    namespaces built when the index loads (TypeScript) answer exactly as ``ph``, with the same journal."""
    del case
    monkeypatch.setattr(sdk_module, "SDK_EAGER_MAX_TOOLS", 0)
    seen = {way: await _run_case(server, tmp_path, way, options, calls) for way in _ways()}
    for way in seen:
        layout = sorted(p.name for p in (tmp_path / way / "pipeshub" / "session" / "sdk" / "python" / "pipeshub_tools").iterdir())
        assert "_lazy.json" in layout and not any(p.startswith("transfer") for p in layout), (way, layout)
    answers, entries, runs, holds = seen["ph"]
    for way, (stub_answers, stub_entries, stub_runs, stub_holds) in seen.items():
        assert [a for a, _ in stub_answers] == [a for a, _ in answers], way
        assert (stub_entries, stub_runs, stub_holds) == (entries, runs, holds), way
