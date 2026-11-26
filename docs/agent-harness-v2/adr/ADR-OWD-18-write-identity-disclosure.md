# ADR-OWD-18 — Identity and AI disclosure on external writes

## Context

[OWD-18](../one-way-doors.md): how commits, emails, messages and tickets created by agents are attributed downstream. Git history, mail archives and ticket audit keep this forever, so changing it later leaves inconsistent historic attribution.

## Decision

Adopt OWD-18. Per action type, the org chooses between acting as the user with a disclosure footer or header ("via PipesHub agent X", with a session link) and a bot identity with `Co-authored-by: <user>`. The setting is applied in the toolset write path and the Tool Gateway, not by the harness. Git commits are signed with a bot key held outside the sandbox. Every external write carries `session_id` where the target supports metadata.

## As built

Audited at `959c3bda72`. No earlier ADR. No disclosure footer or header, no bot identity and no `Co-authored-by` handling exist in `backend/python/app/`, `backend/python/harness_core/` or Node. The harness has no git push route, so commit signing does not apply yet.

### WP-23 (branch `harness/v2-wp23-writeid`)

- **The setting.** `writeIdentity.email|message|ticket|document|event|commit`: per action type, `user` or `bot`, `flat` ceilings exposed to the deployment and the org (the deployment may fix one; else the org chooses), pinned at admission like every dimension. Unset is `user`. Saved through the policy section route (`writeIdentity`).
- **Applied in the toolset write path.** `app/harness/write_identity`: a PRE_TOOL_USE middleware in the executor every tool call goes through, installed by the native loop's assembly and by the engines' Tool Gateway (`RegistryToolPort`), so both apply the same rule. A disclosed write gets "Sent via PipesHub agent {agent} · View the session" with the session's link (the web app's `publicEndpoint`; the session's id without it), in the target's markup: plain text, Markdown, HTML (Confluence storage format, SharePoint, OneNote, Outlook HTML bodies) or Slack mrkdwn. A text that already carries it is left alone; a structured body (a Jira ADF document) is the connector's. A create without the text gets the disclosure as its text when the tool declares that argument.
- **Which writes.** `targets.py` names, per connector tool, the argument people read (64 tools over 16 toolsets); every other external write on the catalog is exempt with its reason (reactions, records, code runs, edits inside a document, containers, reads). `test_wp23_write_identity.py` holds both lists to the real catalog, so a new connector write fails until it is placed.
- **`session_id`.** Every disclosed write carries the session in its link or its text.
- Tests: `test_wp23_write_identity.py` (WID-1..WID-10, one WID-2 case per disclosed tool).

### WP-90 (branch `harness/v2-wp90`)

- **Bot accounts.** A bot account is a service-account agent's own connection to a toolset instance, which admins already connect in that agent's settings; no new sign-in flow. The org names one per instance in `writeIdentity.bots` (`{instanceId: agentKey}`), an org-only `flat` dimension pinned with the action types, saved through the same section route (If-Match, `config.changed`), its shape checked by Node (`control-plane/domain/write-identity.ts`).
- **Admission.** `app/harness/write_identity/bots.py` keeps an instance's account only when the run uses the instance, some action type is `bot`, and the agent is a service account of the run's org with a signed-in connection; it admits exactly that (instance, agent) pair to the run's `ToolCredentials`, which then read it from its owner path and never from the pin's binding of the actor.
- **The write.** The loader binds that instance's writes of the bot-sent action types to a client built on the bot's connection (`ToolInstanceCreator.create_bot_instance`) and records their paths; the middleware sends those with "Sent by PipesHub agent {agent} for {person}" (a commit also `Co-authored-by: {person} <{email}>`) and refuses every other bot write as before. The admin UI offers the bot choice once an app has a bot account (owner amendment 2026-10-29).
- **Wording per action type.** One fixed sentence per kind: Sent via (emails, messages), Written via (tickets, documents), Created via (events), Committed via (commits); the bot's names the person. The admin's preview shows each, in every locale (WID-11).
- **Instance paths.** One of several instances of a type registers `/tools/<type>_<tag>/<tool>`; the middleware looked targets up by that path, so such writes carried no disclosure and a `bot` setting did not refuse them. It now reads the path the tool declares (WID-19).
- **Today's loop.** Unchanged, as the 2026-10-29 scope amendment says; the STATUS row's "apply it in today's loop" is resolved by that amendment, not built.
- Tests: `test_wp90_bot_accounts.py` (WID-12..WID-19), wp90-admin-ui.http.test.ts (moved out of this repository), `frontend/app/(main)/workspace/ai-access/__tests__/wp90-bot-accounts.test.tsx`.

### WP-132 (branch `harness/v2-wp132`)

- **The identity is an extension point.** `WriteIdentityPort` (`app/harness/write_identity/ports.py`: `prepare` before the tools load, `settings` for the run, `tools` per toolset instance), read with `harness_edition().port`. This build's `UserWriteIdentity` sends every write from the user's own account with the fixed disclosure and the session link, whatever the pin's `writeIdentity.*` holds; the loop's assembly, the engines' durable assembly and the tool loader each call the port.
- **Settings answer the rule.** `WriteIdentitySettings` (`disclosure.py`) carries `identity`, `sentence_for`, `co_author` and `refusal`; the middleware and the pull request tool ask `refusal` and `sentence_for`, so a port's settings may name another sender or refuse a write without changing the executor.
- **Node and the web app.** The `writeIdentity` policy section is an edition section (refused `CP_NOT_EXPOSED` without a rule); AI access shows the disclosure read only (`AiAccessWriteIdentity`); the dry run names no bot-sent kind (`dryRunBotActions`).
- **Kept as shared vocabulary and mechanism.** The `writeIdentity.*` dimensions and enums, `RunPolicy.write_identities()` and `bot_accounts()`, `ToolCredentials.admit_bot`, `ToolInstanceCreator.create_bot_instance` and the context's per-run extension state (`AgentContext.extension_state`, where a port keeps what it confirmed), so a port plugs in without patching shared code.
- Tests: `test_wp132_write_identity_port.py` (WIP-1..WIP-6), `test_wp23_write_identity.py`, `policy-section-rules.test.ts`, `wp90-admin-ui.http.test.ts`, `wp23-agent-data.test.tsx`, `agent-environment-extras.test.tsx`.

## Deviations

1. **Bot identity.** A bot account is a service-account agent's connection (WP-90), not a vendor-side bot user PipesHub creates. A connector instance without one still refuses the org's `bot` writes ("not sent", with what the admin can do), never sending them as the user without the disclosure the org chose.
2. **Commit signing.** WP-26 built the push route (GIT-FLOW); signing with a bot key held outside the sandbox waits on the credential rotation (OB-1). Until then a commit carries the disclosure (and, sent as bot, `Co-authored-by`) in its message.
3. **Sent wording's language.** A run pins no reader language, so the disclosure a write carries is the English product wording; the admin's preview shows it in their language.

## Owner amendments

- **2026-10-29 (scope):** the disclosure applies to harness runs only; today's legacy chat loop stays unchanged (`DURABLE_ONLY`; the parity lanes leave out exactly the disclosure suffix, `tests/harness_replay/wire.py`).
- **2026-10-29 (bot option):** hidden in the admin UI until a connector has a bot account; a `bot` setting stays refused at the write. The card shows such a setting as "not sent", with "Send from the user's account".
- **2026-10-29 (wording):** fixed product text, translated in every locale; admins only see a preview (`controlPlane.agentData.who.disclosure`, held to `DISCLOSURE` by WID-11).
- **2026-10-29 (placement):** a card, "Who agent messages are from", on the existing Workspace › AI Access page; a setting the deployment fixed shows a lock.

## Status

**Accepted** (2026-10-26). Write identity per action type, the AI disclosure and its admin card built in WP-23 (2026-10-29); bot accounts and the wording per action type in WP-90 (2026-11-06); commit signing open (above).
