# ADR-MA-01: An engine run's model is decided on one path, whatever account pays for it

- Fixes: SEC-C-02.
- Amends: A1 (the account path AD-2/AD-5/AD-13) and AG1 ("a client-supplied `modelAccountId` keeps A1's meaning").
- Related: G-41 (model groups), ADR-OWD-08 (model gateway).

## Context

`ModelAccessService` had two decision paths. With no account named, `configured()` resolved the model against the org's configured entry: the key must exist and the name must be one it lists. With an account named (`modelAccountId`), `account()` checked who owns the account and its provider, then pinned whatever `modelName` the client sent. Nothing checked that name against the configured models, and an `org_cloud` account (`cloud/<id>`) was accepted from any member.

`ModelGroupStep` reads the model key of the turn. On the account path no key was resolved, so the step checked the org's default entry instead of the model the run then used.

As a result, a member could hand off to an engine on an org cloud account, with a model nobody configured, paid by the org, and outside their model groups. The per-run USD cap was the only limit.

## Decision

1. **One resolver.** Every engine run's model goes through `ModelAccessService.resolveModel`: on the org key, a person's own key, or an org cloud account; whether the client named the account or the agent chose it.
   - The entry is the one the request keys, or the one the agent grants; otherwise it is the org's default entry.
   - The model name must be one that entry lists, compared exactly as configured. There is no case folding, trimming or Unicode normalisation of the request, so a variant name is refused instead of being matched.
   - With no key named, a name is looked up only among the default entry's models. That is the entry `ModelGroupStep` checks for such a turn, so the group check and the resolved model cannot disagree.
   - With no name named, the entry's first model is used, on every account kind. A missing name on an account is no longer `account_model_required`, so that code now has no producer.
   - The account only changes who pays. It never widens which model may be used. The account's provider must be the resolved entry's provider (`account_provider_mismatch`).
2. **An org cloud account is chosen by the agent's editor.** A client may name `cloud/<id>` only when it is the account the agent pins (`modelAccountId`). That is the same run the agent-chosen path makes. Any other cloud id is refused `account_not_owned` before the store is read, so the refusal also tells nobody whether the account exists. A member never points org spend at an account.
3. **A person's own key is also held to the configured models.** It is decided from first principles, not by who pays:
   - What goes to the model is org data, such as knowledge, connectors and tool results. The configured model list is the admin's approval of where that data may go, and where a configured model may be served (MG-15) is an extension point.
   - G-41 limits models by group whoever pays.
   - Keeping one path removes the class of fault behind this finding.

   A person keeps their choice of any configured model from their key's provider, paid by them. A model the org never configured is asked for through the admin.
4. **The groups are held to the resolved model at the spawn too.** `ChildRunService.childEngineAccess` checks the actor's model groups again on `decided.access.model.modelKey` (`MODEL_GROUP_REQUIRED`), with the same live read as admission. A removal from a group between admission and the hand-off therefore refuses the hand-off, and an account path can never carry a model past the group check.
5. **Refusals name what to do.**
   - `route_not_configured` lists the entry's configured names (at most 10) and says to pick one or ask the admin. It never echoes the name the client asked for.
   - The org-cloud refusal says that the agent's editor chooses the account, and to send without naming one.
   - The chat words every model access refusal of a hand-off with the existing `model_access` notice. The calling model reads the specific message in the tool result, so no new code or locale string is needed.
6. **Pins carry the configured entry on every account kind.** An account route's grant now has `modelKey` and `modelName`, as the org-key route always had. The route id and the routes digest are unchanged.

7. **A person's own Claude plan is held to the org's model policy too** (2026-11-13, amending this ADR). The plan path no longer pins Claude Code's own default (`plan-default`): it resolves a configured model through `resolveModel`, as every other path does, and pins its key, so `ModelGroupStep` (and the hand-off re-check) hold it to the person's model groups.
   - A turn or agent that names a model is held to it; one that names none runs the org's first configured Anthropic model.
   - The plan reaches Anthropic's models only: another provider's model is refused `route_not_configured`, naming the Claude models to pick; an org with none configured is told to ask its admin or turn off "Use my Claude account".
   - The worker runs exactly that model on the plan (`--model` and every Claude Code model variable set to it), so neither the CLI's default nor a sub-agent or small-model setting reaches another model. A pin from before this change (`plan-default`) keeps Claude Code's default.
8. **A group refusal names what the person may use**, on every path: `MODEL_GROUP_REQUIRED` from admission and from the hand-off lists the configured models the person's groups allow (`GroupAccess.allowedModelNames`, at most 10), or says none is open to them now.

## As built

Built by R38-AUTHZ on `harness/v2-r38authz`. Node: `ModelAccessService.resolveModel` and `account()` (`application/model-access-service.ts`) and the hand-off group re-check (`application/child-run-service.ts`), committed as `990b93c850`. Tests: the `SEC-C-02` proof in `tests/modules/agent-harness/sec-c-platform-appsec.test.ts` and the bypass cases in `sec-c02-model-access-one-path.test.ts`. Python:

- The model gateway already confined every call to the grant's routes (`ModelNotGranted`, with the routes digest bound into the token).
- The gateway re-check is now built. `ConfiguredModelAccess._resolve_account` (`app/harness/access/configured.py`) requires the entry the pin keys (`config_key`), of the account's provider, to list the model name exactly. A pin made before Node keyed account routes is found by provider and name. Anything else is refused `route_not_configured` (403), and no transport is built.
- The check runs after the account is read, so a store outage stays a retryable `account_unavailable`.
- Tests: `tests/unit/harness/access/test_sec_c02_account_model.py`.

- Decisions 7 and 8 (R38-AUTHZ follow-up): `ModelAccessService.planModel`, `GroupAccess.allowedModelNames`, `HarnessGroupAccessRefusedError(code, allowedModels)`; worker `PlanSignIn.model`, `own_login_launch(launch, model)`. Tests: N `engine-signin.test.ts` MA-PLAN-1..4, `sec-c02-model-access-one-path.test.ts` C02-12; H `test_signin.py::test_r38_*`, `test_vendor_hosts.py::test_r38_*`.

## Deviations

None from the decision. Behaviour that callers see change:

- A client that named `cloud/<id>` on an agent that pins none is refused. The browser never sends `modelAccountId` (AG1 D-1), so only API callers see this. They drop the field, or the agent's editor pins the account.
- An engine turn naming a `modelName` with no `modelKey` must name a model of the default entry. Before this change, the configured path ignored such a name.
- A person on their own Claude plan in an org whose model groups exclude every configured Claude model is refused, where before the plan ran Claude Code's default; the plan's small and sub-agent models are the pinned model too, which may cost the person's plan more than Claude Code's own choices.
- `account_model_required` no longer has a producer. It stays in the error catalog until the catalog is next revised.

## Owner amendments

None. Decided by R38-AUTHZ (2026-11-13) from first principles, long term; decision 7 added 2026-11-13.

## Status

**Accepted** (2026-11-13): the decision for SEC-C-02, built by R38-AUTHZ.
