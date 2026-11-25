# ADR-WEB-01: browser security headers are the product default, and the pages' CSP is built from the pages

- Fixes SEC-D-07 (and SEC-C-14) (R41-OPS, 2026-11-13).
- Related: SEC-D-07, SEC-C-14, [ADR-KEY-02](ADR-KEY-02-per-component-keys.md).

## Context

- Helmet, the CSP and `frame-ancestors` ran only with `STRICT_MODE=true`, and every Compose file shipped `false`. A default install
  served `/` with no CSP, no framing protection and `X-Powered-By: Express`; only `/login`, `/sign-up`, `/oauth` and
  `/slack/link` were unframeable.
- The strict-mode CSP, had anyone turned it on, would have broken the UI: the Next.js static export bootstraps each
  page with inline scripts (the RSC payload, the theme and icon-font scripts), and its `script-src` allowed none.
- With no CSP, a stored or reflected XSS in rendered model output runs and can send the session tokens in
  `localStorage` anywhere; and any site, a `*.pipeshub.com` sibling included, can frame the chat and clickjack an
  approval card.

## Decision

**D1. The headers are on for every response, whatever the environment says.** `securityHeaders()`
(`backend/nodejs/apps/src/libs/middlewares/security-headers.ts`) runs first in the middleware chain:

- `Content-Security-Policy` (D2), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy` (camera, geolocation, payment, USB and
  display capture off; the microphone for this origin only, for dictation), and helmet's small defaults.
- `Strict-Transport-Security: max-age=31536000` when the request arrived over HTTPS (`req.secure`, so
  `X-Forwarded-Proto` counts only from a proxy `TRUST_PROXY` trusts) or `FRONTEND_PUBLIC_URL` is `https://`. Browsers
  ignore HSTS on plain HTTP, so a local install is never pinned. No `includeSubDomains` or `preload`: those bind sibling
  sites the install does not own.
- `X-Powered-By` is off (`app.disable('x-powered-by')`, on the public app as on the internal listener).
- Cross-origin opener and resource policies stay unset: the MSAL sign-in popup reports back to its opener, and the
  desktop app (`file://`) loads this server's images.

**D2. Three policies, chosen by what the response is.**

- *Pages* (every HTML file of the static export, whether `express.static`, the SPA fallback or the 404 page serves it):
  `script-src 'self'` plus the SHA-256 of each inline script **in that file**, computed from the file and memoised
  per file version (`ShellScriptHashes`). No `'unsafe-inline'` and no `'unsafe-eval'`: the export's own scripts run,
  and an injected one does not. `'wasm-unsafe-eval'` lets the Lottie player compile its WebAssembly and allows no
  JavaScript eval. `connect-src 'self'` plus the sign-in providers and the connector service's public origin, so a
  script cannot `fetch` a token off-origin. `img-src` keeps `https:`, because a person may choose to load a remote
  image in an answer (`SafeMarkdownImage` loads only trusted hosts by itself). `object-src 'none'`, `base-uri 'self'`,
  `form-action 'self'`, `frame-src 'self' blob:` (PDF previews) plus the sign-in providers. Untrusted HTML previews keep
  their sandboxed `srcdoc` frame, which inherits this policy and adds its own nonce policy.
- *Everything else* (JSON, files, streams, errors): `default-src 'none'; base-uri 'none'; form-action 'none'`, so an
  uploaded HTML or SVG file opened from the API runs nothing.
- *The API reference* (`/api/v1/docs`): a fixed HTML page of ours with inline handlers, Google Fonts and a "try it"
  console that may call any instance, so `'unsafe-inline'` scripts and an open `connect-src`. It renders no
  user content.

Hashes rather than nonces: the pages are static files, so the hashes are exact and need no per-request HTML rewrite, and
the files can still be cached and served by `express.static`.

**D3. The CSP is enforced from this release, not report-only first.** The finding proposed a report-only release. The
hash policy is derived from the very files served, the UI was walked under it with zero violations (D6), and every
Playwright spec on the base fixture now fails on a CSP violation, so a page that breaks is found by the tests, not by
users. An operator who meets a broken page sets `CSP_REPORT_ONLY=true`: the policy is then reported, not enforced
(framing stays enforced, as `frame-ancestors` is ignored in report-only policies). Browsers report violations to
`POST /api/v1/csp-report` (D5).

**D4. `STRICT_MODE` is retired; relaxations have their own names.** An existing `.env` carries `STRICT_MODE=false`, so
letting it relax anything would have kept every upgraded install as open as before. It is no longer read; when set, the
app logs once what replaces it. The relaxations are explicit:

- `CSP_FRAME_ANCESTORS`: origins that may frame the app (an intranet portal). The sign-in, sign-up, OAuth, Slack-link
  and agent-claim pages stay unframeable whatever it says (`isUnframeablePage`, matched case-insensitively, after
  percent-decoding and collapsing repeated slashes, as Express routes them). With an allowlist, `X-Frame-Options` is
  omitted, since it cannot name origins.
- `CSP_CONNECT_SRCS`, `CSP_SCRIPT_SRCS`, `CSP_FRAME_SRCS`: extra host sources, added to the built-in lists (an
  edition adds its own through `EditionBoot.cspSources`). Only host sources are taken: a keyword (`'unsafe-inline'`,
  `*`), a scheme-wide source (`https:`, `data:`) or a second directive (`;`) in the value is dropped.
- `CSP_REPORT_ONLY` (D3).

**D5. Violation reports are logged, small and capped.** `POST /api/v1/csp-report` takes both report formats, at most
16 KB, keeps only the directive, the refused URL's origin (a path or query can carry a token) and the page's path, logs
at most 30 a minute per process (`csp.violation`, then one `csp.violation.dropped` count) and answers `204`.

**D6. The UI is verified under the policy.** The base Playwright fixture fails any spec that meets a CSP violation in
the console (a spec that provokes one on purpose says so with `EXPECTS_CSP_VIOLATION`).
`frontend/tests/e2e/security/browser-security-headers.spec.ts` walks the main pages signed in, checks their headers,
keeps a screenshot of each, proves the chat cannot be framed and that an injected inline script does not run.

**D7. The frontend fetches nothing from public CDNs.** The Lottie player fetched its WebAssembly from jsDelivr and
unpkg; it now loads the copy bundled with the app (`setWasmUrl`), which also fixes the animation on an offline install.

## As built

- Node: `backend/nodejs/apps/src/libs/middlewares/security-headers.ts`, `csp-report.ts`, `no-framing.middleware.ts`
  (`isUnframeablePage`, `/agent/claim` added); `backend/nodejs/apps/src/app.ts` (wiring, `x-powered-by`);
  `pipeshub-openapi.yaml` (`/csp-report`).
- Frontend: `frontend/app/components/ui/lottie-loader.tsx`; `frontend/tests/e2e/fixtures/base.fixture.ts`
  (`failOnCspViolation`); `frontend/tests/e2e/security/browser-security-headers.spec.ts`.
- Compose: no file sets `STRICT_MODE`; `docker-compose.yml` passes the `CSP_*` knobs.
- Tests: `backend/nodejs/apps/tests/libs/middlewares/security-headers.test.ts` (headers on pages, a 404, API routes and
  the docs; hashes; HSTS with and without a trusted proxy; `STRICT_MODE` values; the embed allowlist and its bypass
  spellings; source-list injection; report-only; reports), `backend/nodejs/apps/tests/app.test.ts` (the real
  application, with `STRICT_MODE` unset, `false` and `true`),
  `backend/python/tests/unit/harness/security/test_sec_d_ops_supply_chain.py` (the SEC-D-07 proofs, green),
  `backend/nodejs/apps/tests/modules/agent-harness/sec-c-platform-appsec.test.ts` (SEC-C-14, green).

## Deviations

- **Session tokens stay in `localStorage` for now (R41OPS-FU-7).** *Closed by [ADR-WEB-02](ADR-WEB-02-session-cookie-and-health.md) (R43-AUTH); the cookie path cannot be limited to the refresh route under `__Host-`, which requires `Path=/` (ADR-WEB-02 D2).* Moving them is right, and it is not one header: the
  web app, the desktop app, the Slack bot's link flow, the MCP OAuth pages and every `Authorization: Bearer` caller in
  the frontend read them. With script-src limited to the pages' own hashes and `connect-src` to this origin and the
  sign-in providers, an XSS can neither run nor send a token away, which is what made the token placement dangerous.
  The plan:
  1. Node sets the refresh token as a `__Host-` cookie (`HttpOnly; Secure; SameSite=Strict`, its path limited to the refresh route)
     on sign-in and refresh, and the refresh route reads it from the cookie (keeping the body for one release).
  2. The frontend keeps the access token in memory only (a module-level store), refreshing silently on load; the
     `localStorage` keys are deleted on first load of the new build.
  3. State-changing cookie routes need a CSRF check (`SameSite=Strict` plus an `Origin` check).
  4. The desktop app, which is `file://` and cannot hold a `Secure` cookie for the server, keeps its token in the OS
     keychain through the Electron bridge.
  5. A Playwright journey proves no `jwt_*` key in storage after sign-in and that a refresh survives a reload.
- **Unauthenticated `/api/v1/health` still names the stores (R41OPS-FU-8).** *Closed by [ADR-WEB-02](ADR-WEB-02-session-cookie-and-health.md) D8 (R43-AUTH); the detail moved behind an org admin's token, not to the internal listener.* The workspace Services page and the lane's
  readiness (`agentHarness.capabilities: settled`) read that detail, so moving it is a change to both. Plan: the public
  route answers `{status, agentHarness: {capabilities}}` only; the detail moves to `/api/v1/health/services`-style routes
  behind an admin session (the Services page) and to the internal listener (lanes and Prometheus). `/api/v1/docs`
  stays public: it documents a public API and holds no instance data.

## Owner amendments

None.

## Status

**Accepted** 2026-11-13 under the owner's direction of 2026-11-12 (first principles, long term, best UX).
