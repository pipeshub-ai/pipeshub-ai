# First-run without a browser

After Docker is up, someone still has to create the first organization, configure an LLM, and mint a personal access token so an agent can call `/mcp`.

Those APIs already exist. This script is the supported way to call them from a terminal. Do not curl them from a coding-agent chat: `POST /api/v1/personal-access-tokens` returns the secret in JSON, so the token would land in the transcript.

The script writes the token to `--token-file` (mode `0600`) and never prints it.

## Rules

1. **Write the token to a file, never to stdout.** There is no `--print-token`, and the script will not accept a token as an argument.
2. **Mark onboarding complete** with `PUT /api/v1/org/onboarding-status` `{ "status": "configured" }`. Otherwise the first dashboard visit is still the wizard. That field is a UI gate, not an authorization check.
3. **Use this script's payloads.** Do not reverse-engineer the settings UI from a browser session.
4. **Connector OAuth stays in a browser.** Slack, Drive, and Jira still need a human. This script does not connect them.

## What the script calls

| Step | Method | Path | Notes |
| --- | --- | --- | --- |
| Empty check | `GET` | `/api/v1/org/exists` | Public. `{exists:true}` means an org already exists, not that search works. The script **exits** if true. |
| First org | `POST` | `/api/v1/org` | Unauthenticated. Requires `accountType`. Whoever reaches a fresh instance first owns it. |
| Login | `POST` | `/api/v1/userAccount/initAuth` | `x-session-token` is a **response header**. |
| | `POST` | `/api/v1/userAccount/authenticate` | Needs that header. Body: `method` + `credentials`. Turnstile if `TURNSTILE_SECRET_KEY` is set. |
| LLM | `POST` | `/api/v1/configurationManager/ai-models/providers` | Session JWT + admin. Provider-shaped `configuration`. |
| PAT | `POST` | `/api/v1/personal-access-tokens` | Not `/api/v1/pat`. **Always send `scopes`.** Omitting them grants the full `mcpScopes` set. |
| Wizard | `PUT` | `/api/v1/org/onboarding-status` | `{ "status": "configured" }` |

PAT scopes the script mints (valid on the stock `MCP_SCOPES` list):

```
conversation:chat
semantic:write
kb:read
user:read
connector:read
```

`semantic:write` is what *runs* a search. `config:read` is omitted on purpose (`llmModels` on sources comes back empty without it).

## Usage

The instance must already be up (`GET /api/v1/health/services` — that is the installer, not this script). Origin default `http://localhost:3000`.

```bash
cp bootstrap-first-run.env.example bootstrap-first-run.env
# edit the env file in an editor — do not paste secrets into a chat
./bootstrap-first-run.sh --env-file ./bootstrap-first-run.env \
  --token-file "$HOME/.config/pipeshub/token"
```

The script refuses a public DNS origin unless `PIPESHUB_ALLOW_NONLOCAL=1`, and that override still requires `https://`. Keep first-run on localhost: `POST /api/v1/org` is whoever-reaches-it-first.

## Tests

Do not run this against an instance that already has an organization (it 400s on `POST /org`). Do not print the token file.

Without Docker: `bash deployment/docker-compose/tests/bootstrap_first_run_test.sh`.

## What this script does not do

- Install or start Docker (the instance must already be running)
- Connect Slack, Drive, or Jira (those need a browser)
- Log in with a device code (that is a separate OAuth flow)
