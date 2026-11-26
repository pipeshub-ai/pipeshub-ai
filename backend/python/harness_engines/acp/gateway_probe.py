"""The tool gateway lists its tools to the sandbox before an agent with no MCP status API opens its session (E2E3-F6).

Gemini CLI 0.63.0 over ACP connects its MCP servers inside ``session/new`` and ``session/load`` and waits there for
one that accepts and answers slowly; one that refuses (the egress proxy answers 503 while nothing listens behind
it) is left disconnected for the life of the session, is never tried again, and every prompt goes out without any
gateway tool. It reports none of this: no ACP request, no status API, nothing on stderr (verified on the pinned
binary, WP-59). So the driver does what the agent is about to do, from where it does it: inside the sandbox, with
the agent's own runtime, to the URL and with the placeholder the agent is given, it sends MCP ``initialize`` and
``tools/list`` until the gateway lists at least one tool, and only then opens the session. A gateway that does not
within ``GATEWAY_TIMEOUT_S`` is an engine that never came up (``EngineUnhealthy``): the attempt is released with no
session opened and no model call made, and the run resumes in a fresh attempt.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from harness_core.engines.binary import run_check
from harness_core.engines.errors import EngineUnhealthy
from harness_engines.opencode_api.readiness import GATEWAY_TIMEOUT_S, RECONNECT_PAUSE_S

if TYPE_CHECKING:
    from harness_core.logs import HarnessLog
    from harness_core.sandbox import SandboxHandle

# The placeholder reaches the script by env, never argv (argv is visible to every process in the sandbox).
TOKEN_ENV = "PXH_GATEWAY_PROBE_TOKEN"
# The probe's own outcome: "ready", or why not, in a few words without anything it was sent or answered.
_REASON_LIMIT = 64

# Node (18+: fetch, AbortSignal.timeout). argv: the gateway URL, the bound in ms. Exit 0 and "ready" only when the
# gateway answered initialize and listed one tool or more; it answers JSON or an SSE stream (Streamable HTTP).
PROBE_SCRIPT = r"""
const [url, ms] = process.argv.slice(1);
const signal = AbortSignal.timeout(Number(ms));
const base = {"content-type": "application/json", "accept": "application/json, text/event-stream",
  "authorization": "Bearer " + (process.env.PXH_GATEWAY_PROBE_TOKEN || "")};
const done = (code, why) => { process.stdout.write(why + "\n"); process.exit(code); };
async function rpc(message, extra) {
  const r = await fetch(url, {method: "POST", headers: {...base, ...extra}, body: JSON.stringify(message), signal});
  const text = await r.text();
  if (!r.ok) done(2, "http " + r.status);
  if (!("id" in message)) return {headers: r.headers};
  let reply = null;
  if ((r.headers.get("content-type") || "").includes("text/event-stream")) {
    for (const line of text.split(/\r?\n/)) {
      if (!line.startsWith("data:")) continue;
      try { const m = JSON.parse(line.slice(5)); if (m && m.id === message.id) reply = m; } catch {}
    }
  } else if (text) { try { reply = JSON.parse(text); } catch {} }
  if (!reply || reply.error || !reply.result) done(3, message.method + " refused");
  return {headers: r.headers, result: reply.result};
}
(async () => {
  const init = await rpc({jsonrpc: "2.0", id: 1, method: "initialize", params: {protocolVersion: "2025-06-18",
    capabilities: {}, clientInfo: {name: "pipeshub-gateway-probe", version: "1"}}}, {});
  const extra = {"mcp-protocol-version": String(init.result.protocolVersion || "2025-06-18")};
  const session = init.headers.get("mcp-session-id");
  if (session) extra["mcp-session-id"] = session;
  await rpc({jsonrpc: "2.0", method: "notifications/initialized"}, extra);
  const listed = await rpc({jsonrpc: "2.0", id: 2, method: "tools/list", params: {}}, extra);
  const tools = listed.result.tools;
  if (session) { try { await fetch(url, {method: "DELETE", headers: {...base, ...extra}, signal}); } catch {} }
  if (!Array.isArray(tools) || tools.length === 0) done(4, "no tools");
  done(0, "ready");
})().catch((e) => done(5, e && e.name === "TimeoutError" ? "timeout" : "unreachable"));
"""


async def gateway_lists_tools(
    sandbox: SandboxHandle, *, runtime: str, url: str, placeholder: str, timeout_s: float
) -> tuple[bool, str]:
    """One ask: whether the gateway at ``url`` lists its tools to this sandbox, and the probe's reason."""
    code, printed = await run_check(
        sandbox,
        [runtime, "-e", PROBE_SCRIPT, url, str(max(1, int(timeout_s * 1000)))],
        {TOKEN_ENV: placeholder},
    )
    reason = printed.strip().splitlines()[-1][:_REASON_LIMIT] if printed.strip() else f"exit {code}"
    return code == 0, reason


async def await_gateway_tools(
    sandbox: SandboxHandle,
    log: HarnessLog,
    *,
    runtime: str,
    server: str,
    url: str,
    placeholder: str,
    timeout_s: float = GATEWAY_TIMEOUT_S,
) -> None:
    """Returns once the tool gateway (the agent's MCP server named ``server``) lists its tools from inside the
    sandbox; ``EngineUnhealthy`` if it does not within ``timeout_s``."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    reason = "unasked"
    asks = 0
    while (remaining := deadline - loop.time()) > 0:
        if asks:
            await asyncio.sleep(min(RECONNECT_PAUSE_S, remaining))
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
        asks += 1
        # One ask may take what is left: a gateway that accepts and answers slowly is waited for, as the agent
        # itself waits for it; asking again would only start the slow answer over.
        ready, reason = await gateway_lists_tools(
            sandbox, runtime=runtime, url=url, placeholder=placeholder, timeout_s=remaining
        )
        if ready:
            if asks > 1:
                log.info("engine.tool_gateway.connected", server=server, asks=asks)
            return
    log.warning(
        "engine.tool_gateway.unconnected",
        server=server,
        status=reason,
        asks=asks,
        reasonCode=EngineUnhealthy.reason_code,
    )
    raise EngineUnhealthy(f"the tool gateway did not list its tools within {timeout_s:g} s ({reason})")


__all__ = ["PROBE_SCRIPT", "TOKEN_ENV", "await_gateway_tools", "gateway_lists_tools"]
