# Scripted first-run (mint-to-file)

This is the **supported** way to finish first-run without the browser. Discovering that the settings panel is a client of existing APIs is **not** the same as the work being done. Agents must run `bootstrap-first-run.sh`, not invent curls.

## Headline

`POST /api/v1/personal-access-tokens` returns the secret in JSON (`pat.controller.ts` `createToken` → `token.accessToken`). If an agent calls that endpoint, the PAT lands in the model transcript. This script writes the secret to `--token-file` (mode `0600`) and never prints it.

## Four bullets (do not "fix" these away)

1. **Mint-to-file / mint-to-keychain, never mint-to-stdout.** There is no `auth set`, no `--print-token`, and no token CLI argument.
2. **Flip onboarding status** with `PUT /api/v1/org/onboarding-status` `{ "status": "configured" }`. Scripted bootstrap otherwise leaves `notConfigured` and the first dashboard visit is the wizard. This field is a UI gate, not an authorization check.
3. **Stable payloads** are what this script sends. Do not reverse-engineer the UI in a chat.
4. **Connector OAuth stays human.** Slack / Drive / Jira still need a browser. This is not "answered from Drive in one session."

## What the script calls

| Step | Method | Path | Notes |
| --- | --- | --- | --- |
| Empty check | `GET` | `/api/v1/org/exists` | Public. `{exists:true}` is "org claimed", not "search works". Script **refuses** if true. |
| First org | `POST` | `/api/v1/org` | Unauthenticated. Requires `accountType`. First-claimer-wins. |
| Login | `POST` | `/api/v1/userAccount/initAuth` | `x-session-token` is a **response header**. |
| | `POST` | `/api/v1/userAccount/authenticate` | Needs that header. Body: `method` + `credentials`. Turnstile if `TURNSTILE_SECRET_KEY` is set. |
| LLM | `POST` | `/api/v1/configurationManager/ai-models/providers` | Session JWT + admin. Provider-shaped `configuration`. |
| PAT | `POST` | `/api/v1/personal-access-tokens` | Not `/api/v1/pat`. **Always send `scopes`.** Omitting them grants the full `mcpScopes` set. |
| Wizard | `PUT` | `/api/v1/org/onboarding-status` | `{ "status": "configured" }` |

PAT scopes (agent preset, mintable on stock `MCP_SCOPES`):

```
conversation:chat
semantic:write
kb:read
user:read
connector:read
```

`semantic:write` is what *runs* a search. `config:read` is deliberately omitted (`llmModels` on sources empties without it).

## Usage

Instance must already be up (`GET /api/v1/health/services` — that is the installer, not this script). Origin default `http://localhost:3000`.

```bash
cp bootstrap-first-run.env.example bootstrap-first-run.env
# edit the env file in an editor — do not paste secrets into chat
./bootstrap-first-run.sh --env-file ./bootstrap-first-run.env \
  --token-file "$HOME/.config/pipeshub/token"
```

The script refuses a public DNS origin unless `PIPESHUB_ALLOW_NONLOCAL=1`. Keep first-run on localhost: `POST /api/v1/org` is whoever-reaches-it-first.

## Live proof

A **throwaway empty** stack. Do not run this against an instance that already has an org (it 400s on `POST /org`). Do not mint a PAT into a chat log.

Tests without Docker: `bash deployment/docker-compose/tests/bootstrap_first_run_test.sh`.

## Not this track

- Thin/eval image (RAM/time of Docker).
- Hosted SaaS trial / shared `https://app.pipeshub.com/mcp`.
- OAuth device grant / dynamic client registration.
- Unattended connector OAuth.
