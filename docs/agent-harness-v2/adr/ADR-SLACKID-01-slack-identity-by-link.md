# ADR-SLACKID-01: A Slack person acts in PipesHub only as the account they linked

- Fixes SEC-C-03 (R38-IDENT, 2026-11-13). Amended by R40-SLACK (2026-11-13): links are their organisation's own (D6), the
  seams any composition builds on (D7), and what the bot posts (SEC-B-03, D8).
- Amends: the Slack bot's identity mapping (R35-SLACK, D-SLACK-ASK), and `hydrateScopedRequestAsUser` for Slack tokens.
- Related: SEC-C-03, ADR-OWD-02 (identity and delegation).

## Context

The bot took the sender's Slack profile email (`users.info`), put it in a scoped token, and Node answered as the
PipesHub user with that address (`getLiveByEmail`). A Slack profile email is an attribute anyone with admin rights
over that Slack account can set. In a Slack Connect channel that is another organisation's admin. Inside the home
workspace it is a Slack admin who is not a PipesHub admin. So anyone who can edit a Slack profile email could become
any PipesHub user, the CFO included, and read their knowledge and connectors. No team, guest, stranger or deleted
check existed anywhere. The shared fault is that identity was inferred from a weaker channel's editable attribute
instead of bound by PipesHub's own sign-in.

## Decision

1. **The link comes first.** A Slack identity is the pair (Slack team id, Slack user id). It is bound to one
   PipesHub account only when the person signs in to PipesHub and approves the link. From then on the bot and Node
   match on team id and user id only. Neither ever reads the profile email for identity.
   - On the first message from an unlinked member, the bot asks Node (`POST /configurationManager/internal/slack-bot/identity`,
     `slack-bot:verify` scope) and posts the returned sign-in link **ephemerally** to that person only.
   - The link token is 32 random bytes and stored only as its SHA-256. It is single-use: the first decision spends
     it by compare-and-set, so a replay or a second tab is refused. It is short-lived (10 minutes). It is bound
     server-side to the team id, user id and bot that asked. A newer link retires the older one.
   - The `/slack/link` page requires a PipesHub session in the bot's organisation, by a person (not a service
     account). It names both sides ("Slack user X in workspace T will act as your PipesHub account Y") before
     anything is bound. Approving is a separate click. The page is in Node's unframeable list.
   - Links live in the KV store (`/services/slackIdentity/…`) through `KeyValueStoreService`. That works on every
     KV backend and every document store.
2. **Outsiders are refused, and nothing is looked up for them.**
   - The receiver drops a signed delivery whose event names another workspace (`user_team`, `source_team` or
     `team` different from the envelope's `team_id`, or no `team_id`). It also drops a delivery from a channel
     shared with another organisation (`is_ext_shared_channel`), even from a home member, because the answer would
     be posted where outsiders read it. In both cases it answers 200 and posts a plain ephemeral refusal.
   - Events from bots (`bot_id`, `bot_profile`, `bot_message`) are dropped silently, so two bots cannot loop.
   - The comparison is exact. A case or Unicode variant of the home id is another workspace. Link keys accept only
     Slack's id alphabet (`[A-Z0-9]`).
   - In the handler, before any thread, file or link work, `users.info` must show a member of the bot's own
     workspace (`auth.test`'s `team_id`, which must also equal the event's). The member must not be a guest
     (`is_restricted`, `is_ultra_restricted`), not `is_stranger`, not `deleted`, and not a bot or app user.
     Otherwise the bot refuses privately and says what to do.
3. **A changed identity never moves a link.** A Slack email change changes nothing, because the email is not read.
   A link is revoked when its PipesHub account is disabled, removed or made a service account. This is checked at
   every use, and the link is deleted when the check fails, so a re-enabled account links again. Every Slack token
   is re-resolved by Node (`hydrateScopedRequestAsUser`): a token that names a bot (`configId`) is answered only
   through the link, whatever email it carries.
4. **Service-account agents.** An agent that runs as its own service account still answers an unlinked workspace
   member. The member's stand-in identity is derived from their Slack ids (`slack-<team>-<user>@slack.invalid`),
   never from their profile email. For any other bot an unlinked member gets only the link.
5. **Migration.** Existing Slack users keep working only after they link once. The first message after upgrade gets
   the link and a plain line saying why: "PipesHub no longer goes by the email in your Slack profile, so nobody
   can act as you by changing theirs." Admins see every link, and whether its account is still active, under
   Workspace → Bots. They can unlink any of them.

6. **A link is its organisation's own** (R40-SLACK amendment). One Slack workspace can hold the bots of several
   organisations, so a link is a row of the document store (`slack-user-link`: Mongo `slack_user_links` with the unique
   key (orgId, workspaceId, slackUserId), PostgreSQL `integrations.slack_user_links` under the org row policy), never a
   deployment-wide KV entry. Every read, revoke and list names the organisation; linking in one never moves or removes
   another's link. A link request (still a single-use KV entry, stored only as its hash) names the organisation whose
   bot asked (`orgId`, required). The page and the decision refuse a person of another organisation
   (`403 HTTP_SLACK_LINK_OTHER_ORG`, translated on the page) and spend nothing. A request from before requests named
   their organisation is refused as not valid, so the person asks for a new link. The bot names its organisation when
   it asks who a person is, and Node refuses an organisation it does not serve. The links are removed with their
   person (`removeForUser`) and with their organisation (`removeForOrg`, the org's deletion and its erasure hooks).
7. **The seams** (R40-SLACK amendment). Edition-neutral entry points, each taking the organisation from its caller
   (this edition: the install's only one, `slack_link/sole-org.ts`):
   - `identity/edge.ts` `refuseForeignSlackEvents` (the receiver's guard); `identity/home.ts` `resolveSlackHome`,
     `withHome` (the bot's own workspace from `auth.test`, recorded at registry load);
   - `identity/actor.ts` `resolveSlackActor({ bot, client, message, orgId, eventTeamId, botUserId })`: the person
     check, then that organisation's answer, or null after telling the sender;
   - `scoped-request.ts` `linkedUser(orgId, { teamId, slackUserId }, links?)` and
     `bindScopedRequestToSlackAccount(req, appConfig, orgId, agentConfigs, links?)`; `hydrateScopedRequestAsUser` is
     this edition's composition of them;
   - the repository (`get`, `upsert`, `remove`, `listForOrg`, `removeForUser`, `removeForOrg`).
   - the identity handlers (`resolveSlackCaller`, `inspectSlackLinkRequest`, `decideSlackLinkRequest`,
     `listSlackIdentityLinks`, `revokeSlackIdentityLink`) take an optional `SlackIdentityOrgs` (`botOf`, `personOrg`,
     `adminOrg`); the default, `soleOrgSlackIdentityOrgs`, is this edition's: the stored bot list and the install's
     only organisation.
8. **What the bot posts** (SEC-B-03). Model and other untrusted text reaches Slack only through one escaper
   (`utils/slack-escape.ts`): `&`, `<`, `>` are escaped with no exceptions, so `<!channel>`, `<!here>`, `<!subteam^…>`,
   `<@U…>` and `<url|label>` in it are shown as text, whatever their encoding, nesting or invisible characters. A link
   is built only by `slackLink` from parsed markdown, and a label that names another host shows the real target.
   Bot-authored notices (the refusals and the link above) keep their own `<url|label>`. The hand-off names a waiting
   tool display-safe (ADR-TEXT-01).

9. **Who a Slack person acts as is audited** (R44-IDENT amendment, R38IDENT-FU-5). Each change is one entry on the
   organisation's audit chain, on the Slack person's own stream (`slackid:<team>:<user>`), committed before the answer:
   - `identity.slack_linked` when a person approves a link: who approved it (also the account it binds), the Slack
     team and user ids, and `moved` with the earlier account when the approval moved the link from another person.
     A link that cannot be recorded is not kept: the earlier state comes back and the person asks for a new link.
   - `identity.slack_unlinked` when an admin removes a link (`by` the admin, reason `admin_removed`) or when PipesHub
     removes it at its next use (`actor: system`, reason `account_removed`, `account_disabled` or `service_account`).
     An unlink stays done when the record cannot be written, and the gap is logged
     (`slack_identity.unlink_not_audited`): taking authority away never waits on the audit store.

   Entries hold ids and reason codes only, never a token or a Slack name. The admin audit search shows both kinds
   under Configuration ("Linked a Slack account", "Moved a Slack account to another person", "Removed a Slack
   account link: …"). Links erased with their person or organisation (D6) are not recorded one by one: the deletion
   is the event, and an organisation's chain goes with it.

## As built

- Link store: `backend/nodejs/apps/src/modules/configuration_manager/slack-identity/slack-identity-links.ts` (requests in KV, `/services/slackIdentity/…`; links in `libs/repositories/slack-user-link`, migration `integrations_0003`); liveness and revocation: `slack-linked-account.ts`; routes: `slack-identity.controller.ts`, mounted in `cm_routes.ts`, in `pipeshub-openapi.yaml`.
- Slack edge: `refuseForeignSlackEvents` in `backend/nodejs/apps/src/integrations/slack-bot/src/identity/edge.ts`, mounted by `receiver.ts`; `slackEventRefusal`, `slackPersonRefusal` and the plain wording in `caller-identity.ts`; `resolveSlackActor` (`identity/actor.ts`, called by `admitSlackCaller` in `index.ts`) runs before any thread, file or link work.
- Node: `hydrateScopedRequestAsUser` (`backend/nodejs/apps/src/modules/enterprise_search/utils/scoped-request.ts`) answers a token naming a bot only through the link.
- Audit (R44-IDENT): kinds and entry `backend/nodejs/apps/src/modules/agent-harness/domain/slack-identity-audit.ts`; the chain writer `slack-identity/slack-identity-audit.ts`, wired by `slackIdentityLinks()`. Tests: N `tests/integrations/slack-bot/r44-slack-identity-audit.test.ts`.
- UI: `/slack/link` (`frontend/app/(public)/slack/link/`) and the linked-accounts list on Workspace → Bots.

## Deviations

Considered and rejected:

- **Matching on a verified email** (Slack's `enterprise_user` or a domain check). Still an attribute PipesHub does not
  control, and a Slack admin can change it. Rejected for the link.
- **Answering Slack Connect channels as the service account.** Possible later as an explicit admin opt-in; refused
  by default because an answer there is read by another organisation.

## Owner amendments

None.

## Status

**Accepted** (R38-IDENT, 2026-11-13).

Consequences:


- Each person has to link once. That is the cost of not trusting an attribute PipesHub does not control.
- Slack threads mapped to a person under the old email key start a new conversation after the link.
- Enterprise Grid org-wide installs whose events name a sibling workspace are refused. Allowing a list of trusted
  sibling teams is a later, explicit admin opt-in.
- Slack Connect channels are not served at all. Serving them only as a service account, ephemerally, is a later,
  explicit admin opt-in.
- Links made before the R40-SLACK amendment lived in KV and are not read: those people link once more. `docstore-migrate`
  does not copy the links either (R40SLACK-FU-1).
