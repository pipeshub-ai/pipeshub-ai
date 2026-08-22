# PipesHub + Omnigent

Connect an [Omnigent](https://omnigent.ai) agent to PipesHub's existing MCP
server so the agent can answer from your company's indexed knowledge.

This folder lives in the **PipesHub repository**. It does not change the
PipesHub backend. It is an Omnigent agent definition plus small setup helpers.

```text
Omnigent agent
  -> pipeshub_chat (default) / pipeshub_search / …
  -> PipesHub /mcp
  -> permission-checked company knowledge
  -> grounded answer with sources
```

## Prerequisites

1. **Omnigent** installed and able to run an agent
   (`uv tool install omnigent`, or see https://omnigent.ai).
2. A **running PipesHub** instance with at least one indexed document.
3. A **credential** that can call `/mcp` (details below).

Minimum tested Omnigent version: **0.7.0** (Antigravity harness tested on 0.8.1).

## Quick start (this repository)

```bash
cd integrations/omnigent
./scripts/setup.sh
./scripts/run.sh -p "What is our refund policy?"
```

`setup.sh` asks for:

1. PipesHub URL (example: `http://localhost:3000`)
2. How to authenticate
3. The credential (hidden prompt; never passed as a CLI flag)

It then:

- writes a private credentials file under `.local/` (mode `600`)
- generates `agent/config.yaml` with your MCP URL
- calls MCP `initialize` + `tools/list` and confirms the PipesHub tools exist

`run.sh` loads those credentials in its own process and starts Omnigent.
You do **not** need to run `source .env`.

## Authentication choices

PipesHub contains private data, so `/mcp` requires a credential.

| Method | When to use | How |
| --- | --- | --- |
| Access token | Fastest / portable | Paste a token into `setup.sh`, or set `PIPESHUB_MCP_TOKEN` |
| Password login | Local/self-hosted password orgs only | `setup.sh --auth password --email you@example.com` |
| OAuth client credentials | CI / service accounts | Set `PIPESHUB_CLIENT_ID` and `PIPESHUB_CLIENT_SECRET` |

Examples:

```bash
# Token from the environment (non-interactive)
PIPESHUB_MCP_TOKEN='...' ./scripts/setup.sh --url http://localhost:3000 --auth token --non-interactive

# Local password login
./scripts/setup.sh --url http://localhost:3000 --auth password --email admin@example.com

# OAuth app for CI
PIPESHUB_CLIENT_ID='...' PIPESHUB_CLIENT_SECRET='...' \
  ./scripts/setup.sh --url http://localhost:3000 --auth oauth --non-interactive
```

Notes:

- Password login fails for SSO-only, OTP, CAPTCHA, or multi-step orgs. Use a
  token or OAuth app instead.
- OAuth `client_credentials` results use the **OAuth app owner's** permissions,
  not each end user's identity.
- OAuth scopes requested for the full tool surface:
  `semantic:write kb:read conversation:write conversation:chat agent:read agent:execute user:read team:read`.
- Omnigent does not currently complete PipesHub's browser MCP OAuth flow.
  Enterprise SSO users must provide a token obtained outside Omnigent until
  Omnigent adds generic MCP OAuth support.

## What the agent can do

This agent exposes the full PipesHub MCP tool surface. Omnigent shows MCP
tools to the model with a server prefix (`<mcp-server>__<remote-tool>`):

| Tool | Use for |
| --- | --- |
| `pipeshub__pipeshub_chat` | **Default.** Grounded Q&A with citations |
| `pipeshub__pipeshub_search` | Find / locate / list documents |
| `pipeshub__pipeshub_sources` | Discover connectors, KBs, models |
| `pipeshub__pipeshub_directory` | People, groups, teams, `whoami` |
| `pipeshub__pipeshub_download_record` | Download file bytes by `recordId` |
| `pipeshub__pipeshub_agents` | List org agents; then chat with `agentId` |

Routing lives in [`agent/AGENTS.md`](agent/AGENTS.md) and mirrors PipesHub's
own MCP server instructions (chat-first). Omnigent currently does **not**
inject MCP `initialize.instructions` into the model prompt, so the agent
file is the active control plane for this integration.

## Attach PipesHub to an existing Omnigent agent

Copy the inline MCP block from [`agent/config.yaml.example`](agent/config.yaml.example)
into your agent's `config.yaml`, or use
[`agent/tools/mcp/pipeshub.yaml.example`](agent/tools/mcp/pipeshub.yaml.example)
as a starting point for directory-style MCP configs.

Set:

```bash
export PIPESHUB_MCP_TOKEN='...'
omnigent run path/to/your-agent -p "Check PipesHub for our retry policy"
```

Important Omnigent 0.7.0 detail: the `tools:` allowlist is applied for
**inline** MCP entries in `config.yaml`. Directory files under `tools/mcp/`
currently ignore that allowlist, so prefer the inline block when you want a
hard allowlist.

## Layout

```text
integrations/omnigent/
├── README.md
├── VERSION
├── agent/
│   ├── AGENTS.md
│   ├── config.yaml.example
│   └── tools/mcp/pipeshub.yaml.example
├── scripts/
│   ├── setup.sh
│   ├── run.sh
│   ├── mcp-check.py
│   └── lib.sh
└── tests/
    ├── validate_agent.py
    └── scripts_test.sh
```

Generated locally (gitignored):

```text
.local/credentials.env
agent/config.yaml
```

## Validate / test locally

### Offline (no PipesHub required)

```bash
cd integrations/omnigent
./tests/run_all.sh
```

This runs helper tests, the MCP tool-surface contract (all six tools in
config/examples/docs/scopes), and agent structural validation.

### Live MCP tool smoke (all PipesHub MCP tools)

With PipesHub running and credentials from setup:

```bash
cd integrations/omnigent
./scripts/setup.sh --url http://localhost:3000
./tests/run_all.sh --require-live
```

`live_mcp_tools.py` exercises:

| Tool | Smoke call |
| --- | --- |
| `pipeshub_sources` | list sources + LLM models |
| `pipeshub_directory` | `whoami` |
| `pipeshub_agents` | list agents (may be empty) |
| `pipeshub_search` | query with `limit: 3` |
| `pipeshub_download_record` | download first search hit’s `recordId` (skipped if no hits) |
| `pipeshub_chat` | short `internal_search` question |

Optional flags: `--skip-chat`, `--skip-download`.

### Manual Omnigent session checklist

1. `./scripts/setup.sh --url http://localhost:3000`
2. `./scripts/run.sh --harness antigravity --model gemini-2.5-flash` (or another harness)
3. Ask a knowledge question → expect `pipeshub__pipeshub_chat`
4. Ask “find documents about X” → expect `pipeshub__pipeshub_search`
5. Ask “whoami / list users in PipesHub” → expect `pipeshub__pipeshub_directory`
6. Confirm a fresh shell can run `./scripts/run.sh` without exporting env vars

## New to PipesHub?

Start PipesHub with the current Docker Compose installer:

```bash
cd deployment/docker-compose
./install.sh
```

Then open the printed local URL (usually `http://localhost:3000`), create an
account, complete onboarding, and upload a document.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `missing required command: omnigent` | Omnigent not installed | Install Omnigent, then retry |
| `PipesHub URL must be the site origin` | URL includes `/api/v1` or `/mcp` | Use `http://localhost:3000` |
| `non-localhost ... must use https` | Plain HTTP to a remote host | Use `https://...` or localhost |
| MCP auth failed HTTP 401/403 | Bad/expired token or missing scope | Re-run `setup.sh`; for OAuth ensure conversation/agent/user scopes |
| Required tool `pipeshub_chat` not found | Older MCP package / wrong endpoint | Confirm PipesHub version and `/mcp` |
| `unknown tool name: pipeshub__pipeshub_chat` | MCP tools never registered | Usually `${PIPESHUB_MCP_TOKEN}` was not expanded into inline MCP headers. `run.sh` materializes a temp agent with the token resolved — retry without `--resume` / `--continue` |
| Thin / “name only” answers from search | Model used search instead of chat | Start a fresh session; AGENTS.md defaults to chat for Q&A |
| `unexpected extra argument (GEMINI_API_KEY=...)` | Env var passed as a CLI arg | Run `GEMINI_API_KEY=... ./scripts/run.sh ...` |
| `--harness goose-native` rejects AGENT path | Global/default harness is a native TUI | Pass an SDK harness: `--harness antigravity` / `claude-sdk` / `codex` |
| Antigravity protobuf gencode/runtime mismatch | Omnigent 0.8.1 pins `protobuf<7`, but Antigravity gencode needs `>=7.35` | After `uv tool install 'omnigent[antigravity]==0.8.1'`, run: `uv pip install --python ~/.local/share/uv/tools/omnigent/bin/python 'protobuf==7.35.1'` then re-run (fresh session) |
| Banner shows wrong harness (e.g. Ollama) after passing `--harness` | Auto-resumed old conversation | `run.sh` passes `--no-session` by default; quit and re-run |
| Password login rejected | SSO/OTP/multi-step org | Use token or OAuth |
| GET `/mcp` returns 405 | Expected for stateless MCP | Use POST JSON-RPC (the scripts do) |

## Phase 2 (not in this folder)

After this example is proven:

1. Contribute the tested agent to `omnigent-ai/omnigent` for discovery.
2. Propose that Omnigent inject MCP `initialize.instructions` into the agent prompt (so server-side routing works without duplicating it in `AGENTS.md`).
3. Propose generic MCP OAuth/PKCE support in Omnigent for enterprise SSO.
