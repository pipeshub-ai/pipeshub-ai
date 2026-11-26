"""PH-1..PH-19 (recs audit R3; contract v1, OWD-6, WS-15): the ``ph`` CLI an engine runs
from its shell.

``ph`` is one standard-library file (``harness_engines/pipeshub/ph.py``, ``/usr/local/bin/ph`` in every engine
image). These tests run it as the image does (``python3 ph.py``) and in process against a real HTTP server that
answers the way the tool gateway's MCP transport does (JSON-RPC, SSE or JSON): argument parsing, where the gateway
and placeholder come from, the requests it sends, and every failure in plain words with its exit code.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from harness_engines.pipeshub import ph

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

PH = Path(ph.__file__)
PLACEHOLDER = "pxh-tools-0123456789abcdef"
SEARCH = {"name": "search", "description": "Search the knowledge base. Answers cited passages.", "inputSchema": {"type": "object"}}
SLACK = {"name": "slack__send_message", "description": "Send a Slack message to a channel.", "inputSchema": {"type": "object"}}
SEARCH_TOOLS = {"name": "search_tools", "description": "Find tools.", "inputSchema": {"type": "object"}}
CALL_TOOL = {"name": "call_tool", "description": "Call a tool.", "inputSchema": {"type": "object"}}


class Gateway:
    """What the test server answers: ``tools`` for tools/list, ``answer(name, arguments)`` for tools/call."""

    def __init__(self) -> None:
        self.tools: list[dict[str, Any]] = [SEARCH, SLACK]
        self.requests: list[tuple[dict[str, str], dict[str, Any]]] = []
        self.status = 200
        self.headers: dict[str, str] = {}
        self.sse = True
        self.answer: Callable[[str, dict[str, Any]], dict[str, Any]] = lambda name, arguments: {
            "content": [{"type": "text", "text": f"{name}:{json.dumps(arguments, sort_keys=True)}"}],
            "structuredContent": {"disposition": "executed", "retryable": False},
            "isError": False,
        }


@pytest.fixture
def gateway() -> Iterator[tuple[Gateway, str]]:
    state = Gateway()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.requests.append(({k.lower(): v for k, v in self.headers.items()}, body))
            if state.status != 200:
                self.send_response(state.status)
                for key, value in state.headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(b'{"error": {"code": "GATEWAY_TOKEN_EXPIRED"}}')
                return
            if body["method"] == "tools/list":
                result: dict[str, Any] = {"tools": state.tools}
            else:
                result = state.answer(body["params"]["name"], body["params"]["arguments"])
            message = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result})
            self.send_response(200)
            if state.sse:
                self.send_header("Content-Type", "text/event-stream")
                payload = f"event: message\ndata: {message}\n\n".encode()
            else:
                self.send_header("Content-Type", "application/json")
                payload = message.encode()
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_address[1]}/k/mcp"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    (tmp_path / "session").mkdir()
    (tmp_path / "VERSION").write_text("1\n")
    return tmp_path


def session(folder: Path, url: str) -> None:
    (folder / "session" / "gateway.json").write_text(json.dumps({"version": "1.0.0", "url": url, "token": PLACEHOLDER}))


def run(argv: list[str], url: str | None = None, *, stdin: str = "") -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    gw = ph.Gateway(url, PLACEHOLDER) if url is not None else None
    code = ph.main(argv, out=out, err=err, stdin=io.StringIO(stdin), gw=gw)
    return code, out.getvalue(), err.getvalue()


def test_ph1_version_names_ph_and_the_folder(folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ph, "FOLDER", str(folder))
    out = io.StringIO()
    assert ph.main(["--version"], out=out) == 0
    assert out.getvalue().strip() == f"ph {ph.PH_VERSION} (/pipeshub contract 1)"
    (folder / "session" / "VERSION").write_text("2\n")
    assert ph.version_text(str(folder)) == f"ph {ph.PH_VERSION} (/pipeshub contract 1; this turn's session folder was written for contract 2)"


@pytest.mark.parametrize(
    ("argv", "says"),
    [
        (["call"], "the following arguments are required: tool"),
        (["search"], "the following arguments are required: query"),
        (["frobnicate"], "invalid choice"),
        (["call", "search", "--json", "{not json"], "--json is not valid JSON"),
        (["call", "search", "--json", "[1, 2]"], "--json must be a JSON object"),
    ],
)
def test_ph2_usage_errors_are_plain_and_exit_64(argv: list[str], says: str) -> None:
    code, out, err = run(argv)
    assert code == ph.EX_USAGE
    assert says in err and err.startswith("ph: ") and out == ""


def test_ph3_the_gateway_and_placeholder_come_from_the_session_folder_or_the_environment(folder: Path) -> None:
    session(folder, "http://tool-gateway:8097/mcp")
    assert ph.gateway(str(folder), env={}) == ("http://tool-gateway:8097/mcp", PLACEHOLDER)
    env = {ph.URL_ENV: "http://other/mcp", ph.TOKEN_ENV: "pxh-tools-env"}
    assert ph.gateway(str(folder), env=env) == ("http://other/mcp", "pxh-tools-env")


def test_ph4_outside_an_engine_run_ph_says_so(tmp_path: Path) -> None:
    with pytest.raises(ph.PhError) as missing:
        ph.gateway(str(tmp_path), env={})
    assert missing.value.code == ph.EX_CONFIG
    assert "has no PipesHub tool gateway" in str(missing.value) and "inside a PipesHub engine run" in str(missing.value)


def test_ph5_a_call_is_one_mcp_tools_call_with_the_placeholder(gateway: tuple[Gateway, str]) -> None:
    state, url = gateway
    code, out, err = run(["call", "search", "--json", '{"q": "q3"}'], url)
    assert (code, err) == (0, "")
    assert out.strip() == 'search:{"q": "q3"}'
    headers, body = state.requests[-1]
    assert body["method"] == "tools/call"
    assert body["params"] == {
        "name": "search",
        "arguments": {"q": "q3"},
        # SPENDCONT: ph prints or redirects the result, so it asks the gateway for all of it.
        "_meta": {"pipeshub/fullResult": True, "progressToken": body["id"]},
    }
    assert body["jsonrpc"] == "2.0"
    assert headers["authorization"] == f"Bearer {PLACEHOLDER}"
    assert "text/event-stream" in headers["accept"] and "application/json" in headers["accept"]


def test_ph5_json_answers_and_arguments_from_stdin(gateway: tuple[Gateway, str]) -> None:
    state, url = gateway
    state.sse = False
    code, out, _ = run(["call", "search", "--json", "-"], url, stdin='{"q": "from stdin"}')
    assert (code, out.strip()) == (0, 'search:{"q": "from stdin"}')


def test_ph6_a_tool_the_list_defers_is_called_through_call_tool_as_the_engine_would(gateway: tuple[Gateway, str]) -> None:
    state, url = gateway
    state.tools = [SEARCH_TOOLS, CALL_TOOL]
    code, _, _ = run(["call", "slack__send_message", "--json", '{"text": "hi"}'], url)
    assert code == 0
    params = dict(state.requests[-1][1]["params"])
    params.pop("_meta")
    assert params == {"name": "call_tool", "arguments": {"name": "slack__send_message", "arguments": {"text": "hi"}}}
    code, _, err = run(["call", "call_tool", "--json", "{}"], url)
    assert code == ph.EX_USAGE and "name the tool itself" in err


def test_ph7_a_failed_tool_exits_1_with_its_words_on_stderr(gateway: tuple[Gateway, str]) -> None:
    state, url = gateway
    state.answer = lambda name, arguments: {
        "content": [{"type": "text", "text": "Denied by your organization's policy."}],
        "structuredContent": {"disposition": "denied", "retryable": False, "reasonCode": "denied"},
        "isError": True,
    }
    code, out, err = run(["call", "slack__send_message", "--json", "{}"], url)
    assert (code, out, err.strip()) == (ph.EX_TOOL_ERROR, "", "Denied by your organization's policy.")


def test_ph8_a_call_held_for_approval_prints_the_hold_and_exits_75(gateway: tuple[Gateway, str]) -> None:
    state, url = gateway
    text = "Waiting for a person to approve this call (interaction int_1). End this turn."
    state.answer = lambda name, arguments: {
        "content": [{"type": "text", "text": text}],
        "structuredContent": {"disposition": "deferred", "retryable": False, "deferred": True},
        "isError": False,
    }
    code, out, _ = run(["call", "slack__send_message", "--json", "{}"], url)
    assert (code, out.strip()) == (ph.EX_TEMPFAIL, text)


def test_ph9_a_refused_credential_says_the_run_may_have_ended(gateway: tuple[Gateway, str]) -> None:
    state, url = gateway
    state.status, state.headers = 401, {"x-gateway-refusal": "token_expired"}
    code, out, err = run(["call", "search", "--json", "{}"], url)
    assert (code, out) == (ph.EX_NOPERM, "")
    assert "refused this sandbox's credentials (token_expired)" in err and "The run may have ended" in err


def test_ph10_an_unreachable_gateway_is_named() -> None:
    code, _, err = run(["search", "jira"], "http://127.0.0.1:9/k/mcp")
    assert code == ph.EX_UNAVAILABLE
    assert err.startswith("ph: could not reach the PipesHub tool gateway at http://127.0.0.1:9/k/mcp")


def test_ph11_tools_search_uses_the_gateways_search_tools_or_ranks_the_listed_tools(gateway: tuple[Gateway, str]) -> None:
    state, url = gateway
    code, out, _ = run(["tools", "search", "send", "slack", "message"], url)
    assert (code, out.splitlines()) == (0, ["slack__send_message: Send a Slack message to a channel"])
    state.tools = [SEARCH_TOOLS, CALL_TOOL]
    state.answer = lambda name, arguments: {
        "content": [{"type": "text", "text": json.dumps([SLACK])}],
        "structuredContent": {"disposition": "executed", "retryable": False},
        "isError": False,
    }
    code, out, _ = run(["tools", "search", "slack", "--limit", "50"], url)
    assert (code, out.splitlines()) == (0, ["slack__send_message: Send a Slack message to a channel"])
    assert state.requests[-1][1]["params"] == {"name": "search_tools", "arguments": {"query": "slack", "limit": 20}}
    assert "_meta" not in state.requests[0][1]["params"]
    state.answer = lambda name, arguments: {
        "content": [{"type": "text", "text": "No tool of this run matches 'zzz'. Try other words."}],
        "structuredContent": {"disposition": "executed", "retryable": False},
        "isError": False,
    }
    code, out, _ = run(["tools", "search", "zzz"], url)
    assert (code, out.strip()) == (0, "No tool of this run matches 'zzz'. Try other words.")


def test_ph12_describe_prints_the_schema_and_names_a_missing_tool(gateway: tuple[Gateway, str]) -> None:
    _, url = gateway
    code, out, _ = run(["describe", "search"], url)
    assert code == 0
    assert out.startswith("search\n\nSearch the knowledge base. Answers cited passages.\n\nArguments (JSON schema):")
    assert json.loads(out.split("Arguments (JSON schema):\n", 1)[1]) == {"type": "object"}
    code, _, err = run(["describe", "nope"], url)
    assert code == ph.EX_DATAERR and "no tool named 'nope'" in err and "ph tools search" in err


def test_ph13_the_file_runs_as_the_image_runs_it(folder: Path, gateway: tuple[Gateway, str]) -> None:
    """As ``/usr/local/bin/ph``: its own interpreter, standard library only, the session folder from the environment."""
    _, url = gateway
    session(folder, url)
    env = {"PIPESHUB_FOLDER": str(folder), "PATH": "/usr/bin:/bin"}
    done = subprocess.run(
        [sys.executable, "-I", str(PH), "call", "search", "--json", '{"q": "x"}'],
        capture_output=True, text=True, env=env, check=False, timeout=60,
    )
    assert (done.returncode, done.stdout.strip(), done.stderr) == (0, 'search:{"q": "x"}', "")
    version = subprocess.run(
        [sys.executable, "-I", str(PH), "--version"], capture_output=True, text=True, env=env, check=False, timeout=60
    )
    assert version.stdout.strip() == f"ph {ph.PH_VERSION} (/pipeshub contract 1)"
    assert PH.read_text().startswith("#!/usr/bin/env python3\n")


INDEX = "knowledgegraph__search: Search indexed knowledge by meaning\nslack__send_message: Send a Slack message\n"
GOLDEN = Path(__file__).with_name("ph_json_golden.json")


def seeded(folder: Path, url: str, index: str = INDEX) -> None:
    """The session folder as the controller seeds it for a turn: the gateway, the tool index and the contract."""
    session(folder, url)
    (folder / "session" / "tools.txt").write_text(index)
    (folder / "session" / "VERSION").write_text("1\n")


def test_ph14_ws15_tools_list_reads_the_turns_index_and_needs_no_gateway(
    folder: Path, gateway: tuple[Gateway, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    state, url = gateway
    monkeypatch.setattr(ph, "FOLDER", str(folder))
    seeded(folder, url)
    code, out, err = run(["tools", "list"])
    assert (code, err) == (0, "")
    assert out.splitlines() == ["knowledgegraph__search: Search indexed knowledge by meaning", "slack__send_message: Send a Slack message"]
    assert state.requests == []
    # Without a seeded index (ph run by hand with the environment), the gateway lists them.
    (folder / "session" / "tools.txt").unlink()
    code, out, _ = run(["tools", "list"], url)
    assert (code, out.splitlines()) == (0, ["search: Search the knowledge base", "slack__send_message: Send a Slack message to a channel"])


def test_ph15_ws15_the_older_names_run_the_canonical_commands(gateway: tuple[Gateway, str]) -> None:
    """``ph describe`` and ``ph call`` (what engines, skills and goldens were written with) answer exactly as
    ``ph tools describe`` and ``ph tools call`` and send the same requests."""
    state, url = gateway
    for older, canonical in (
        (["describe", "search"], ["tools", "describe", "search"]),
        (["call", "search", "--json", '{"q": 1}'], ["tools", "call", "search", "--json", '{"q": 1}']),
    ):
        state.requests.clear()
        said = run(older, url)
        sent = [body for _, body in state.requests]
        state.requests.clear()
        assert run(canonical, url) == said, older
        assert [body for _, body in state.requests] == sent
    code, out, _ = run(["--help"])
    assert code == 0
    assert "Canonical commands: search, fetch, tools {list,search,describe,call}" in out
    assert "older name of: ph tools describe" in out and "older name of: ph tools call" in out


def test_ph16_ws15_search_and_fetch_read_company_knowledge(
    folder: Path, gateway: tuple[Gateway, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    state, url = gateway
    monkeypatch.setattr(ph, "FOLDER", str(folder))
    seeded(folder, url, INDEX + "knowledgegraph__fetch_record: Read whole records\n")
    code, out, _ = run(["search", "q3", "revenue"], url)
    assert (code, out.strip()) == (0, 'knowledgegraph__search:{"query": "q3 revenue"}')
    assert state.requests[-1][1]["params"]["arguments"] == {"query": "q3 revenue"}
    code, out, _ = run(["fetch", "r-1", "r-2"], url)
    assert (code, out.strip()) == (0, 'knowledgegraph__fetch_record:{"record_ids": ["r-1", "r-2"]}')


def test_ph17_ws15_a_run_without_the_knowledge_tool_is_told_how_to_find_what_it_has(
    folder: Path, gateway: tuple[Gateway, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    state, url = gateway
    monkeypatch.setattr(ph, "FOLDER", str(folder))
    seeded(folder, url, "slack__send_message: Send a Slack message\n")
    code, out, err = run(["search", "q3"], url)
    assert (code, out) == (ph.EX_DATAERR, "")
    assert err.strip() == (
        "ph: this run has no knowledge search tool (knowledgegraph__search). "
        "See what it has with: ph tools search <what you need>"
    )
    assert state.requests == []


def _golden_cases(url: str) -> dict[str, tuple[list[str], str | None]]:
    """Each ``--json`` case: argv and the gateway URL ``ph`` is handed (``None``: from the session folder)."""
    return {
        "version": (["--json", "--version"], None),
        "tools.list": (["--json", "tools", "list"], None),
        "tools.list trailing --json": (["tools", "list", "--json"], None),
        "tools.search": (["--json", "tools", "search", "slack", "message"], url),
        "tools.describe": (["--json", "tools", "describe", "search"], url),
        "tools.call": (["--json", "tools", "call", "search", "--json", '{"q": "q3"}'], url),
        "tools.call older name": (["--json", "call", "search", "--json", '{"q": "q3"}'], url),
        "tools.call bad arguments": (["--json", "tools", "call", "search", "--json", "[1]"], url),
        "tools.describe unknown tool": (["--json", "tools", "describe", "nope"], url),
        "search": (["--json", "search", "q3", "revenue"], url),
        "fetch without the tool": (["--json", "fetch", "r-1"], url),
        "no command": (["--json"], None),
        "unknown command": (["--json", "frobnicate"], None),
        "gateway unreachable": (["--json", "tools", "search", "x"], "http://127.0.0.1:9/k/mcp"),
        "skill.list without skills": (["--json", "skill", "list"], None),
        "skill.show unknown skill": (["--json", "skill", "show", "nope"], None),
        "policy.check allowed": (["--json", "policy", "check", "shell"], url),
        "policy.check without a tool": (["--json", "policy", "check"], url),
        "ask without a person": (["--json", "ask", "Which", "region?"], url),
        "publish a name outside the outputs folder": (["--json", "publish", "x.md", "--as", "../x.md"], url),
        "publish outside a run": (["--json", "publish", "x.md"], url),
    }


def _answer(argv: list[str], url: str | None) -> dict[str, Any]:
    code, out, err = run(argv, url)
    lines = out.splitlines()
    assert len(lines) == 1 and err == "", (argv, out, err)
    envelope = json.loads(lines[0])
    assert envelope["ok"] is (code == 0)
    assert envelope["ok"] or envelope["error"]["exit"] == code
    return {"exit": code, "stdout": envelope}


def test_ph18_c3_13_json_mode_prints_one_stable_envelope_per_command_golden(
    folder: Path, gateway: tuple[Gateway, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """C3.13 / WS-15: ``ph --json`` prints exactly one JSON object on stdout for every command, success or not,
    exits with the plain mode's code, and names each failure with a stable error code. The envelopes are pinned
    in ``ph_json_golden.json``: a change to them is a contract change (OWD-6: additive only)."""
    state, url = gateway
    monkeypatch.setattr(ph, "FOLDER", str(folder))
    seeded(folder, url)
    answers = {name: _answer(argv, given) for name, (argv, given) in _golden_cases(url).items()}
    state.answer = lambda name, arguments: {
        "content": [{"type": "text", "text": "Denied by your organization's policy."}],
        "structuredContent": {"disposition": "denied", "retryable": False, "reasonCode": "denied"},
        "isError": True,
    }
    answers["tools.call refused"] = _answer(["--json", "tools", "call", "slack__send_message", "--json", "{}"], url)
    state.answer = lambda name, arguments: {
        "content": [{"type": "text", "text": "Waiting for a person to approve this call. End this turn."}],
        "structuredContent": {"disposition": "deferred", "retryable": False, "deferred": True},
        "isError": False,
    }
    answers["tools.call held for approval"] = _answer(["--json", "tools", "call", "slack__send_message", "--json", "{}"], url)
    state.status, state.headers = 401, {"x-gateway-refusal": "token_expired"}
    answers["credentials refused"] = _answer(["--json", "tools", "call", "search", "--json", "{}"], url)
    (folder / "session" / "gateway.json").unlink()
    answers["outside a run"] = _answer(["--json", "search", "q3"], None)
    # The test's folder and the host's errno wording are the only parts that vary from one machine to another.
    answers = json.loads(re.sub(r"\[Errno \d+\] ", "", json.dumps(answers).replace(str(folder), "/pipeshub")))
    if os.environ.get("PH_GOLDEN_UPDATE") == "1":
        GOLDEN.write_text(json.dumps(answers, indent=2, ensure_ascii=False) + "\n")
    assert answers == json.loads(GOLDEN.read_text())


def test_ph19_c3_13_every_error_code_is_named_and_documented() -> None:
    codes = {
        ph.EX_TOOL_ERROR, ph.EX_REFUSED, ph.EX_USAGE, ph.EX_DATAERR, ph.EX_UNAVAILABLE, ph.EX_SOFTWARE, ph.EX_TEMPFAIL,
        ph.EX_NOPERM, ph.EX_CONFIG,
    }
    assert set(ph.ERROR_CODES) == codes
    assert len(set(ph.ERROR_CODES.values())) == len(codes)
