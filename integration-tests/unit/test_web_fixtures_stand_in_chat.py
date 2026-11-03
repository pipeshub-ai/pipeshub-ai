"""The web-fixtures stand-in chat model: "OK" by default, scriptable per prompt.

These run the real server in a thread, so they need no stack.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import requests

if TYPE_CHECKING:
    from collections.abc import Iterator

_SERVER = Path(__file__).resolve().parents[1] / "web_fixtures" / "server.py"
_spec = importlib.util.spec_from_file_location("web_fixtures_server", _SERVER)
assert _spec and _spec.loader
server_module = sys.modules.get(_spec.name) or importlib.util.module_from_spec(_spec)
if _spec.name not in sys.modules:
    sys.modules[_spec.name] = server_module
    _spec.loader.exec_module(server_module)


@pytest.fixture
def base(tmp_path: Path) -> Iterator[str]:
    handler = server_module.make_handler(server_module.Site(tmp_path), "http://fixtures")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/__fixtures__"
    finally:
        server.shutdown()
        server.server_close()


def _script(base: str, rules: list[dict]) -> requests.Response:
    return requests.put(f"{base}/openai/script", json={"rules": rules}, timeout=5)


def _ask(base: str, text: str, *, stream: bool = False, **extra: object) -> dict:
    """The assistant message, rebuilt from chunks when streamed, plus finish_reason."""
    payload = {"model": "m", "messages": [{"role": "user", "content": text}], "stream": stream, **extra}
    response = requests.post(f"{base}/openai/v1/chat/completions", json=payload, timeout=5)
    assert response.status_code == 200
    if not stream:
        choice = response.json()["choices"][0]
        return {**choice["message"], "finish_reason": choice["finish_reason"]}
    content, calls, finish = "", {}, None
    for line in response.text.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        choice = json.loads(line[6:])["choices"][0]
        finish = choice["finish_reason"] or finish
        delta = choice["delta"]
        content += delta.get("content") or ""
        for call in delta.get("tool_calls") or []:
            slot = calls.setdefault(call["index"], {"name": "", "arguments": ""})
            slot["name"] += call.get("function", {}).get("name") or ""
            slot["arguments"] += call.get("function", {}).get("arguments") or ""
    message: dict = {"content": content or None, "finish_reason": finish}
    if calls:
        message["tool_calls"] = [{"function": calls[i]} for i in sorted(calls)]
    return message


@pytest.mark.parametrize("stream", [False, True])
def test_without_rules_every_prompt_gets_ok(base: str, stream: bool) -> None:
    assert _ask(base, "anything", stream=stream)["content"] == server_module.STAND_IN_REPLY == "OK"


def test_first_matching_rule_answers(base: str) -> None:
    assert _script(base, [{"match": "refund", "reply": "R1"}, {"match": ".*", "reply": "R2"}]).status_code == 204

    assert _ask(base, "Refund policy?")["content"] == "R1"
    assert _ask(base, "hello")["content"] == "R2"


@pytest.mark.parametrize("stream", [False, True])
def test_tool_call_rule_returns_tool_calls(base: str, stream: bool) -> None:
    _script(base, [{"match": "find", "tool_calls": [{"name": "search", "arguments": {"q": "x"}}]}])

    message = _ask(base, "find it", stream=stream)

    assert message["finish_reason"] == "tool_calls"
    assert message["content"] is None
    assert message["tool_calls"][0]["function"] == {"name": "search", "arguments": json.dumps({"q": "x"})}


def test_json_schema_request_gets_a_minimal_valid_instance(base: str) -> None:
    schema = {
        "type": "object",
        "required": ["topics", "score"],
        "properties": {"topics": {"type": "array"}, "score": {"type": "number"}},
    }
    message = _ask(base, "x", response_format={"type": "json_schema", "json_schema": {"name": "n", "schema": schema}})

    assert json.loads(message["content"]) == {"topics": [], "score": 0}


def test_minimal_instance_covers_scalars_enums_and_unions() -> None:
    mi = server_module.minimal_instance
    assert mi({"type": "string"}) == ""
    assert mi({"type": "string", "enum": ["a", "b"]}) == "a"
    assert mi({"type": "integer"}) == 0
    assert mi({"type": "boolean"}) is False
    assert mi({"anyOf": [{"type": "boolean"}, {"type": "string"}]}) is False
    assert mi({"type": "object", "required": ["a"], "properties": {"a": {"type": "array"}, "b": {"type": "string"}}}) == {"a": []}


def test_bad_regex_is_refused_and_reset_clears_rules(base: str) -> None:
    assert _script(base, [{"match": "(", "reply": "x"}]).status_code == 400
    assert requests.put(f"{base}/openai/script", json={"rules": [{"match": "a"}]}, timeout=5).status_code == 400

    assert _script(base, [{"match": ".*", "reply": "scripted"}]).status_code == 204
    assert _ask(base, "hi")["content"] == "scripted"
    requests.post(f"{base}/reset", timeout=5)
    assert _ask(base, "hi")["content"] == "OK"

    _script(base, [{"match": ".*", "reply": "again"}])
    assert requests.delete(f"{base}/openai/script", timeout=5).status_code == 204
    assert _ask(base, "hi")["content"] == "OK"
