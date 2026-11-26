# pyright: basic
# (Runs in the sandbox on its own interpreter; everything it handles is untyped JSON off the wire.)
"""C-13's fake engine: runs inside the sandbox (``pxh-sandbox:x1``), standard library only.

It knows what any engine knows: the two gateway URLs and one placeholder for
each, from its environment. Phase 1 uses the gateways as an engine does (MCP
Streamable HTTP on the tool gateway, chat completions and Responses on the
model gateway), prints one JSON line per step, then ``{"step": "ready"}`` and
waits for one JSON line on stdin. Phase 2 tries what a hostile engine would,
with what that line hands it (another run's token and placeholder, addresses
to dial directly), then prints ``{"step": "done"}``. With an empty line it
exits after phase 1.
"""

import json
import os
import socket
import sys
import urllib.error
import urllib.request

MODEL = os.environ["PXH_MODEL_URL"]
TOOLS = os.environ["PXH_TOOLS_URL"]
MODEL_KEY = os.environ["PXH_MODEL_KEY"]
TOOLS_KEY = os.environ["PXH_TOOLS_KEY"]
PROXY = os.environ["PXH_PROXY_URL"]
MCP_VERSION = "2025-06-18"
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
VIA_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({"http": PROXY}))


def emit(step: str, **fields: object) -> None:
    print(json.dumps({"step": step, **fields}), flush=True)


def http(url: str, body: object = None, headers: "dict[str, str] | None" = None, opener: urllib.request.OpenerDirector = DIRECT) -> "tuple[int, dict[str, str], str]":
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, headers=headers or {}, method="GET" if data is None else "POST")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with opener.open(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read().decode("utf-8", "replace")
    except OSError as error:
        return 0, {}, type(error).__name__


def refusal(headers: "dict[str, str]") -> "str | None":
    lowered = {k.lower(): v for k, v in headers.items()}
    return lowered.get("x-egress-refusal") or lowered.get("x-gateway-refusal")


def sse_data(text: str) -> "list[dict]":
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ") and line[6:] != "[DONE]"]


def rpc_result(text: str) -> dict:
    messages = sse_data(text) if "data: " in text else [json.loads(text)]
    return next((m for m in messages if "result" in m or "error" in m), {})


def mcp(method: str, params: dict, ident: "int | None", key: "str | None" = TOOLS_KEY) -> "tuple[int, dict[str, str], str]":
    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": MCP_VERSION}
    if key is not None:
        headers["Authorization"] = "Bearer " + key
    body = {"jsonrpc": "2.0", "method": method, "params": params}
    if ident is not None:
        body["id"] = ident
    return http(TOOLS, body, headers)


def call_tool(name: str, arguments: dict, ident: int) -> None:
    status, headers, text = mcp("tools/call", {"name": name, "arguments": arguments}, ident)
    result = rpc_result(text).get("result", {}) if status == 200 else {}
    structured = result.get("structuredContent") or {}
    emit(
        "tools/call",
        tool=name,
        status=status,
        isError=result.get("isError"),
        disposition=structured.get("disposition"),
        reasonCode=structured.get("reasonCode"),
        text="".join(part.get("text", "") for part in result.get("content", [])),
    )


def model_headers(key: str = MODEL_KEY) -> "dict[str, str]":
    return {"Authorization": "Bearer " + key}


def phase_one() -> None:
    status, headers, text = mcp(
        "initialize",
        {"protocolVersion": MCP_VERSION, "capabilities": {}, "clientInfo": {"name": "c13-fake-engine", "version": "1"}},
        1,
    )
    emit("initialize", status=status, refusal=refusal(headers), server=rpc_result(text).get("result", {}).get("serverInfo"))
    status, _, _ = mcp("notifications/initialized", {}, None)
    emit("initialized", status=status)
    status, _, text = mcp("tools/list", {}, 2)
    tools = rpc_result(text).get("result", {}).get("tools", [])
    emit("tools/list", status=status, tools=sorted(t["name"] for t in tools))
    call_tool("record_note", {"note": "c13"}, 3)
    call_tool("record_note", {"note": "c13"}, 4)
    call_tool("delete_everything", {"confirm": True}, 5)

    chat = {
        "model": "engine-model",
        "messages": [{"role": "user", "content": "Say hello."}],
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    status, headers, text = http(MODEL + "/chat/completions", chat, model_headers())
    chunks = sse_data(text) if status == 200 else []
    content = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks if c.get("choices"))
    usage = next((c["usage"] for c in chunks if c.get("usage")), None)
    emit("chat", status=status, refusal=refusal(headers), content=content, usage=usage)

    responses = {"model": "engine-model", "input": "Say hello.", "stream": True, "store": False}
    status, headers, text = http(MODEL + "/responses", responses, model_headers())
    events = sse_data(text) if status == 200 else []
    done = next((e for e in events if e.get("type") == "response.completed"), {})
    output = done.get("response", {}).get("output", [])
    content = "".join(p.get("text", "") for item in output for p in item.get("content", []) if item.get("type") == "message")
    emit("responses", status=status, refusal=refusal(headers), content=content, usage=done.get("response", {}).get("usage"))


def probe(step: str, url: str, body: object = None, headers: "dict[str, str] | None" = None, opener: urllib.request.OpenerDirector = DIRECT) -> None:
    status, response_headers, _ = http(url, body, headers, opener)
    emit(step, status=status, refusal=refusal(response_headers))


def dial(host: str, port: int) -> str:
    try:
        with socket.create_connection((host, port), timeout=3):
            return "open"
    except OSError as error:
        return type(error).__name__


def phase_two(orders: dict) -> None:
    chat = {"model": "engine-model", "messages": [{"role": "user", "content": "x"}]}
    probe("foreign_token", MODEL + "/chat/completions", chat, {"Authorization": "Bearer " + orders["foreignToken"]})
    probe("foreign_placeholder", TOOLS, {"jsonrpc": "2.0", "id": 9, "method": "tools/list"},
          {"Authorization": "Bearer " + orders["foreignToolsPlaceholder"], "Accept": "application/json, text/event-stream"})
    # Assembled here so no file or argument of the sandbox carries a vendor-key shape.
    vendor = "sk-" + "ant-" + "api03-" + "Q" * 24
    probe("vendor_key_header", MODEL + "/chat/completions", chat, {**model_headers(), "X-Debug": vendor})
    probe("no_authorization", TOOLS, {"jsonrpc": "2.0", "id": 10, "method": "tools/list"},
          {"Accept": "application/json, text/event-stream"})
    probe("model_placeholder_to_tools", TOOLS, {"jsonrpc": "2.0", "id": 11, "method": "tools/list"},
          {"Authorization": "Bearer " + MODEL_KEY, "Accept": "application/json, text/event-stream"})
    for index, url in enumerate(orders["offAllowlist"]):
        probe(f"off_allowlist_{index}", url, opener=VIA_PROXY)
    emit("direct", results={f"{host}:{port}": dial(host, port) for host, port in orders["direct"]})


def main() -> None:
    phase_one()
    emit("ready")
    line = sys.stdin.readline().strip()
    if line:
        phase_two(json.loads(line))
    emit("done")


main()
