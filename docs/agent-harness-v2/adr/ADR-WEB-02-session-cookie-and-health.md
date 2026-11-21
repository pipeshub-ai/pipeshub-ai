# ADR-WEB-02: the browser's refresh token is an HttpOnly cookie, the access token lives in memory, and the public health answer names no store

- Fixes R41OPS-FU-7 and R41OPS-FU-8 (R43-AUTH, 2026-11-13), the two deviations [ADR-WEB-01](ADR-WEB-01-browser-security-headers.md) left open.
- Related: SEC-D-07, SEC-C-14, [ADR-CAP-01](ADR-CAP-01-unknown-is-not-none.md) D4 (lanes wait on `agentHarness.capabilities`).

## Context

- The web app kept `jwt_access_token` and `jwt_refresh_token` in `localStorage`. ADR-WEB-01's CSP keeps an injected
  script from running and from sending a token off-origin, but a script that beats the CSP (a hash collision is not
  the worry; a future `'unsafe-inline'` relaxation, a dependency that evals, a same-origin upload served as HTML) could
  read a refresh token good for 30 days.
- Unauthenticated `GET /api/v1/health` returned the store types, the Redis mode, the build and the harness's
  failure detail; `GET /api/v1/health/services` added each deployment check and its detail (the sandbox plane's).
- What must keep working: a person stays signed in across reloads and tabs; the desktop app (`app://` pages);
  PATs, OAuth access tokens, the MCP OAuth pages, the Slack link page and every script or SDK that sends
  `Authorization: Bearer`; the from-source split (Next dev on `:3001`, Express on `:3000`); plain-http installs; and
  every readiness probe (Compose and Helm healthchecks, install.sh, rollback.sh, the release smokes, the e2e lanes'
  `wait_capabilities`, smoke_human.py).

## Decision

**D1. The web app's refresh token is a cookie only the server reads.** A sign-in that sends
`x-pipeshub-session: cookie` (the web app sends it on every `/userAccount/authenticate`) gets the refresh token as a
cookie and the body says `session: cookie` instead of carrying it. A client that does not send the header (the desktop
app, scripts, the SDK, the integration tests) gets the body exactly as before. Opt-in rather than opt-out: nothing that
reads `refreshToken` from the body today changes.

**D2. The cookie's name follows the page's scheme.**

| Page | Name | Attributes |
| --- | --- | --- |
| https | `__Host-pipeshub_refresh` | `HttpOnly; Secure; SameSite=Strict; Path=/`, no `Domain` |
| plain http (laptop install, from-source split, LAN install) | `pipeshub_refresh` | `HttpOnly; SameSite=Strict; Path=/api/v1/userAccount/`, no `Secure` |

The scheme is the page's: the `Origin` header (right behind a TLS-terminating proxy the app does not trust), else
`req.secure`. `__Host-` requires Secure and Path=/, and it is what stops a sibling subdomain or a plain-http response
from planting or overwriting the cookie. A plain-http page cannot keep a Secure cookie in every browser (Safari drops
it even on `localhost`, and a LAN address is never a secure context), so it gets the plain name, limited to the
account routes so it is not sent with every API call. Plain http exposes the access token on every request already;
the cookie adds no new exposure there. **Each scheme reads only its own name**, so a cookie planted over http is never
read by an https page. `Max-Age` is the refresh token's remaining lifetime; no sliding renewal, as before.

The task proposed the non-prefixed name only for `http://localhost`. A LAN install reached over plain http is a
supported, warned-about deployment (ADR-OPS-03 D4); refusing it a session would sign everyone out on every reload
there, so the rule is the scheme, not the host.

**D3. The cookie is read only by the refresh and sign-out routes, and only from a page of this server.**
`refreshTokenFromCookie` (`backend/nodejs/apps/src/modules/auth/session-cookie.ts`) runs before the refresh-token
validator. An `Authorization` header wins and is handled as before (never origin-checked: it is not an ambient
credential). With no header, the cookie is used only when:

- `Sec-Fetch-Site` is not `cross-site`, and
- `Origin` is present (every browser sends it on a POST) and is this server's host, `FRONTEND_PUBLIC_URL` or an
  `ALLOWED_ORIGINS` entry (the from-source split's `http://localhost:3001`).

Otherwise `403`. SameSite=Strict keeps other sites' requests from carrying it; the Origin check also refuses a sibling
subdomain, which is same-site. No other route reads the cookie, so no other route needs CSRF protection. A double-submit
token was not added: with SameSite=Strict, an exact Origin allowlist and no cookie-authenticated route besides these
two, it would protect nothing more.

A refused cookie (`401`) is cleared in the answer, so the browser stops sending it.

**D4. Sign-out clears the cookie whatever happens.** `POST /userAccount/logout/manual` clears both names in every
answer (also when the access token is refused), and with no `Authorization` header the cookie alone names the caller
(a page whose access token has expired can still end its session), under the same Origin rule. Recording `LOGOUT`
ends every refresh token issued before it, as before.

**D5. The desktop app keeps its refresh token in the OS keychain.** Its pages are `app://`, another site from the
server, so they cannot hold the SameSite cookie. Main seals the token with Electron `safeStorage` into
`userData/session-refresh.bin` (mode 0600, atomic rename; `electron/session-vault.ts`) and the renderer reads it
through `electronAPI.session` only to send the refresh call. Without a keychain the token is held in memory for the
process: signed in until the app quits, never written unsealed.

**D6. The access token lives in memory only.** `auth-store.ts` keeps it in the Zustand store and nowhere else. A page
load restores the session with one `POST /refresh/token` (the cookie, or the desktop's keychain) before the guards
render, so a reload, a new tab and an MCP OAuth, Slack link or agent-claim page are signed in without a sign-in screen.
What `localStorage` keeps is `pipeshub_signed_in` = `userId:orgId`: not a credential. It lets a load with nobody signed
in skip the request, and its `storage` events replace the old token events between tabs: removed → this tab signs out;
another account → this tab reloads (as before); set while this tab is signed out → this tab restores. A refused restore
removes the mark; an unreachable server keeps it for the next load.

**D7. The migration runs once, on the first load of the new build.** If `jwt_refresh_token` is in `localStorage`, the
page sends it as a bearer to `/refresh/token` with `x-pipeshub-session: cookie`: the server sets the cookie from it and
answers an access token (desktop: it is sealed into the keychain). Both `jwt_*` keys are then deleted, also when the
server refuses the token; if the old access token is still good the page keeps it in memory, so no one is signed out
mid-task by the upgrade. They stay only when the server could not be reached, to try again on the next load. Sign-out
deletes any left over. A `setTokens(access, refresh)` from a flow that answers in the body (an edition's sign-in
method) takes the same path, so such a flow needs no change.

**D8. The public health answers name no store (R41OPS-FU-8).**

- `GET /api/v1/health` without an org admin's token: `{status, agentHarness: {status, capabilities?}}`. The harness's
  `status` stays because the air-gap smoke waits for `available`; `capabilities` because lanes wait for `settled`
  (ADR-CAP-01 D4). The stores, their modes, `serviceNames`, the build, the warnings, the time and the harness's failure
  `reason`/`detail` are gone.
- `GET /api/v1/health/services` without an admin's token: `{status, services}` with the product's own service names
  only (query, connector, indexing, docling, embedding, parsing, extraction), which every installer, Compose
  healthcheck, rollback.sh and the release smokes read. A deployment's added checks (the sandbox plane's) and their
  `details` are gone.
- An org admin's token (`config:read` for an OAuth token) gets both full reports, so the Services page and the health
  gate's toasts for admins are unchanged; a member gets the public answer, so the gate's member toasts (which never
  named services) are unchanged. A token that does not verify gets the public answer, never an error: a probe must not
  start failing because of a header.
- Not on the internal listener: it exists only with the harness on, while the Compose healthchecks must work without
  it, and lanes read only `capabilities`, which stays public. Prometheus reads `/metrics` there already.
- `published_hub_smoke.sh` reads the build from the app container's `APP_VERSION`/`GIT_COMMIT`, which is where Node
  reads it in a container.
- `/api/v1/docs` stays public (ADR-WEB-01).

**D9. Every refresh replaces the refresh token, and a replayed one ends its sign-in (R43AUTH-FU-1, R44-IDENT,
amendment 2026-11-14).** A copied cookie or a copied desktop token used to work until it expired. Now:

- A sign-in starts a *session family*: one sign-in on one device. The refresh token carries the family (`sid`) and
  its own id (`jti`). The store keeps, per family, the id of the token that may refresh now, the one it replaced and
  when, the end of the sign-in, and whether it was ended. Token ids only, never a token.
- A refresh with the current token replaces it as one compare-and-set, so of two refreshes with one token only one
  rotates. The replacement keeps the sign-in's end: a refresh never extends a session (as D2's `Max-Age`).
- It goes where the old one came from: the cookie for the web (the localStorage move included), `refreshToken` in
  the body for a bearer client (the desktop, scripts), which must keep it.
- **Grace, not a race.** The token just replaced is answered for 60 seconds with the family's current token: a second
  tab, a second desktop window, or a retry after an answer lost on the network. The web app and the desktop also hold
  a Web Lock (`pipeshub-session-refresh`) across tabs and windows for each refresh, so the second one normally sends
  the replacement; the grace covers what the lock cannot (a browser without it, a lost answer).
- **Reuse.** The replaced token after the grace, or any older token of the family, ends the family: whoever holds either
  token signs in again, `401` and the cookie cleared. The first detection is recorded as
  `identity.session_replayed` on the organisation's audit chain (the account and the family, no address, no token)
  and logged as `auth.session_replay_detected`. Other devices' families are untouched; sign-out still ends every
  session (D4).
- **Tokens from before the change** carry no family. The first refresh with one starts a family named by the token's
  hash and answers a replacement, so a second use of the same old token after the grace is caught as well.
  An edition's sign-in method that mints a token in the body is adopted the same way.
- **An edition's own session** (`installEditionRefreshCredential`, R43AUTH-FU-3) refreshes under the same lock. Its
  headers are read inside it, and a replacement answered in the body goes to the keeper the edition installs with
  them (`installEditionRefreshCredential(credential, keep)`) before the next refresh may start. An edition whose
  headers ask for the cookie gets the replacement as the cookie and needs no keeper.
- **Store.** A document-store aggregate, `session-family` (MongoDB `session_families` with a TTL index; PostgreSQL
  `identity.session_families`, `identity_0006`, removed by the TTL sweeper an hour past the end). A family the store
  does not know is refused. The document-store move does not copy families (they name tokens the old store issued):
  after it each person signs in once more.

Grace and lock were chosen over a refresh that never rotates within a window: a lock alone fails on a lost answer,
and a window alone would hand an attacker who refreshes first a token the owner cannot replay-detect.

## As built

- Node: `src/modules/auth/session-cookie.ts`; `routes/userAccount.routes.ts` (refresh and sign-out chains);
  `controller/userAccount.controller.ts` (`completeSignIn`, `getAccessTokenFromRefreshToken`);
  `libs/middlewares/cors.options.ts` (`x-pipeshub-session`); `modules/tokens_manager/routes/health.routes.ts`
  (`healthAudience`, `publicHealth`, `publicServicesHealth`); `pipeshub-openapi.yaml` (`sessionCookie` scheme,
  `SessionDeliveryHeader`, the routes' cookie, 403 and Set-Cookie, `PublicHealthResponse`,
  `PublicServicesHealthResponse`).
- Frontend: `lib/auth/session-credential.ts`; `lib/store/auth-store.ts` (memory store, `restoreSession`, migration);
  `lib/api/token-refresh.ts`, `token-refresh-scheduler.ts`; `lib/api/post-user-account-authenticate.ts`; sign-in
  callers; `electron/session-vault.ts`, `main.ts`, `preload.ts`.
- Deployment: `deployment/docker-compose/tests/published_hub_smoke.sh`.
- Tests: N `tests/modules/auth/routes/session-cookie.http.test.ts` (cookie refresh, CSRF refusals, scheme isolation,
  migration, sign-out), `userAccount.signInFlow.test.ts` (cookie flags per scheme, body kept without the header),
  `userAccount.controller.test.ts`, `userAccount.routes.test.ts`, `health.routes.test.ts` (public shapes, admin,
  member, bad token); F `lib/auth/__tests__/session-restore.test.ts` (restore, migration, desktop),
  `lib/__tests__/no-token-in-web-storage.test.ts` (static: no source writes a token to web storage; only the
  migration names the old keys), the session tests; E `electron/__tests__/session-vault.test.ts`; Playwright
  `tests/e2e/auth/session-persistence.spec.ts` (EM-64), `auth/logout.spec.ts`, the API fixture reading
  `.auth/api-token.json`.

## Deviations

- **No server sign-out in Playwright.** A server sign-out ends every session of the shared e2e account in every
  worker, so the specs drop the session on their own context; the server side is covered by the Node tests.
- **The refresh token does not rotate.** Closed by D9 (R44-IDENT): `src/modules/auth/session-rotation.ts`,
  `session-replay-audit.ts`, `libs/repositories/session-family/`, `backend/db/versions/identity/0006_session_families.sql`;
  frontend `lib/auth/session-credential.ts` (`oneRefreshAtATime`, the desktop's replacement sealed before the lock
  is released). Tests: N `tests/modules/auth/session-rotation.test.ts`, the refresh cases in
  `userAccount.controller.test.ts`; F `lib/auth/__tests__/session-rotation.test.ts`; must-run EM-65.

## Owner amendments

None.

## Status

**Accepted** 2026-11-13 under the owner's direction of 2026-11-12 (first principles, long term, best UX).
