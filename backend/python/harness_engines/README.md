# harness_engines

Engine adapters for the agent harness, one sub-package per engine. Each
implements `harness_core.engines.ports.EngineAdapter` and passes
`harness_core.testing.engines.contract.EngineAdapterContract`. Adapters depend on
`harness_core` only; they never import the embedding application or each other
(import-linter contracts in `backend/python/pyproject.toml`). Installable on its
own (`pyproject.toml` here, with `harness-core` as its one harness dependency).

| Package | Engine | Drive | State |
|---|---|---|---|
| `opencode/` | opencode v1.18.34 (`opencode serve`) | HTTP + `/event` SSE through `SandboxHandle.connect` | `opencode export` JSON in a versioned envelope; restore with `opencode import` |
| `acp/` | any Agent Client Protocol agent described by a signed profile in `acp_agents.json` (kind `acp-<agent>`; shipped: `acp-opencode`, opencode's own `opencode acp`) | ACP v1 JSON-RPC over stdio | `acp-session` envelope: the session id and the agent's session files (`session/load`), or a recap |

`acp/testing/fake_agent.py` is a scripted fake ACP agent (the agent side of the protocol, with switches for
misbehaviour) so the conformance and protocol suites run without a real agent. The adapter contract and its
conformance kit: `docs/agent-harness-v2/guides/adding-a-harness-adapter.md`.

`opencode/testing/fake_server.py` is a recorded fake of the binary (routes,
SSE shapes and event order from the probe captures) so the conformance suite
runs without the real binary.
