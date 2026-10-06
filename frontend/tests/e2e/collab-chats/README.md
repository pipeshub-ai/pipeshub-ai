# Collaborative chats: UI journeys

Playwright specs for the collaborative-chats journeys (80-implementation-plan section 5). Specs are named
`jNN-<slug>.spec.ts`, using the same slugs as the API journeys in `integration-tests/collaborative-chats/`.
They run in the `collab-chats` Playwright project against a real Node API and a real Next.js frontend; only the
Python services are faked. `npm run test:e2e` and the other projects ignore this directory.

| ID | Spec | Owning phase | UI variant |
|---|---|---|---|
| J-03 | `j03-handover.spec.ts` | PH-09 | A shares a chat with B from the drawer (Can continue, with a note); B sends; history is intact and the turns carry the right author chips for both. Axe on the share drawer |
| J-04 | `j04-concurrency.spec.ts` | PH-09 | A is streaming (held by a gate); B sees the busy banner, sends, and the message is queued; when A finishes it is sent once. Axe on the chat with the busy banner |
| J-05 | `j05-revoke-mid-stream.spec.ts` | PH-09 | A revokes B mid-stream; B's slot is marked `accessLost` (banner, no composer, history kept) and evicted on navigation; A's run still completes. Axe on the access-lost banner and on a viewer's read-only banner |
| J-09 | `j09-resume-binding.spec.ts` | PH-09 | B parks a question; A sees it read-only ("Waiting for User Writer"); A's attempts get 403 `RESUME_NOT_ALLOWED` and Python is never called; B answers |
| J-06 | `j06-team-membership.spec.ts` | PH-12 | A removes a team member through the Node Teams route: the member's next request and open tab are denied; adding someone grants it. A change made outside Node is served from cache, with both caches at most 30 s (asserted on the Redis TTLs, no real wait), then denied. Axe on the removed-member banner |
| J-07 | `j07-consent.spec.ts` | PH-12 | B's composer discloses the audience and sends `filesShared: false` without a file; Node's PDP endpoint (`/authz/internal/check`, called as Python calls it) refuses C's read of an unconsented file, allows a consented one, and refuses at once after C is removed. Python's record routes are not on this lane (see the spec header for where they are tested) |
| J-08 | `j08-project-inheritance.spec.ts` | PH-12 | A makes a project chat visible in the access panel (the change dialog lists who gains access); project viewers read it, a ceiling raise lets editors continue, flipping it private removes chat and file access. Axe on the panel and both dialogs |
| J-10 | `j10-cross-user-injection.spec.ts` | PH-12 | The payload Node sends when B writes in a chat A planted an instruction in: B is the sender, A's message carries A's ref, no id or address crosses. A scripted write-guard confirmation card renders for B and read-only for A. The guard itself is PH-08's Python unit tests |
| J-11 | `j11-transfer-owner-inactive.spec.ts` | PH-12 | A transfers from the share drawer (confirm dialog, B owns, A keeps Can continue); with the owner disabled an editor gets the read-only `OWNER_INACTIVE` banner on open or after a refused send; after a transfer to an active user the old owner's state no longer matters |
| J-12 | `j12-mentions.spec.ts` | PH-12 | A human-only mention is a note (no AI call), the mentioned person's inbox gets `chat.mentioned` and nobody else's does, `@assistant` runs the AI. Axe on the note and the inbox |
| J-13 | `j13-agent-from-chat.spec.ts` | PH-12 | Draft card with nothing ticked or created, Create makes a private agent, the other person sees only the redacted line, the `@` picker offers no agent in a plain chat (one verdict shared with the server, #16a) |
| PH12-06 | `flag-off-chat.spec.ts` | PH-12 | All three collaboration flags off: plain textarea composer, a send with the old body, no collaboration chrome or request. The full `tests/e2e/chat/*.spec.ts` run needs a real AI model and the default harness, so it runs in CI only |
| MN-18 (UI) | `mentions-composer.spec.ts` | PH-10 | A types `@`, picks a participant with the keyboard, sends: the stream request carries `mentions` and the wire text; Escape closes without inserting; Shift+Enter adds a line; a typed `<@...>` is sent escaped. Axe on the open popover. The lane runs with `ENABLE_CHAT_MENTIONS` on |

Not a journey: `visual-ph10.spec.ts` is the PH-10 screenshot review. It is skipped unless `PCC_VISUAL_TOUR=1`
(`PCC_VISUAL_TOUR=1 run.sh visual-ph10`) and writes PNGs plus an `INDEX.md` to `test-results/visual-ph10/`.
`visual-ph11.spec.ts` and `visual-ph12.spec.ts` do the same for PH-11 (agent handle, draft card) and PH-12 (owner-inactive
and viewer banners, inbox filter label, every flag off), into `test-results/visual-ph11/` and `test-results/visual-ph12/`.

## Visual tour (opt-in)

`visual-tour.spec.ts` is not a journey: it takes screenshots of every collaborative-chats screen for visual review. It is
skipped unless `PCC_VISUAL_TOUR=1`, so the normal gate never runs it.

```bash
PCC_VISUAL_TOUR=1 frontend/tests/e2e/collab-chats/run.sh visual-tour          # all four combinations
PCC_VISUAL_TOUR=1 frontend/tests/e2e/collab-chats/run.sh visual-tour -g "desktop light"
```

Output: `frontend/test-results/visual-tour/<NN>-<slug>[-element]--<desktop|mobile>--<light|dark>.png` (desktop 1440x900, mobile
375x812; the theme is the `pipeshub-theme-preference` the app reads) and an `INDEX.md` listing every file, the states that
could not be reached and what looked off. Playwright empties `test-results/` at the start of a run, so copy a run away
before starting the next. Notes: the cast is the lane's roster (`User Owner`, `User Writer`, `User Reader`); timestamps are
real; the owner is limited to 20 sharing changes a minute by the Node API, so the tour paces itself and a full run takes
about 25 minutes. The tour switches `ENABLE_PROJECTS` on for one test (control route `/flag` with `key`) and adds teams
through the control route `/team`.

## Run

```bash
frontend/tests/e2e/collab-chats/run.sh                 # every spec here
frontend/tests/e2e/collab-chats/run.sh j04             # extra arguments go to `playwright test`
```

Needs Docker, Node 22 (`~/.local/node/bin`, or `PCC_E2E_NODE_BIN`) and the integration venv (`PCC_E2E_VENV`,
see `integration-tests/collaborative-chats/stack/README.md`). The first run installs missing `node_modules`.
Keep `TMPDIR` short. It pulls `mcr.microsoft.com/playwright:v<@playwright/test version>-jammy` once (override with
`PCC_PW_IMAGE`).

To iterate on a spec without booting the stack each time:

```bash
PCC_E2E_HOLD=1 PCC_E2E_RUN_DIR=/tmp/x frontend/tests/e2e/collab-chats/run.sh      # boots, then waits
PCC_E2E_ATTACH=/tmp/x frontend/tests/e2e/collab-chats/run.sh j05                  # other shell: only Playwright
touch /tmp/x/stop                                                                  # tear down
```

`PCC_E2E_FE_PORT` pins the Next.js port (default: a free one); `PCC_E2E_KEEP_RUN_DIR=1` keeps the logs
(`serve.log`, `next.log`, the Node log).

## What runs

| Piece | Where | Real or fake |
| --- | --- | --- |
| Node API | host process, free port, started from source | real (same as the pytest `collab_stack` lane) |
| MongoDB, Redis | containers `pcc-e2e-<id>-*` | real |
| Next.js | `next dev --turbopack` from `frontend/`, free port, `NEXT_PUBLIC_API_BASE_URL` = the Node API | real |
| Browser | official Playwright image as container `pcc-pw-<id>`, non-root, in its own network namespace; `support/netns-bridge.cjs` relays the Next, Node API and control ports from the host's loopback over Unix sockets in the run directory (`PCC_PW_HOST_NETWORK=1` uses `--network host` instead, where Chromium fails in-flight requests with `net::ERR_NETWORK_CHANGED` whenever a container starts or stops on the host) | real Chromium |
| Python services (query, connectors, indexing, docling, embedding) | one aiohttp fake (`helper/collab_stack/fake_backend.py`) | **fake**: model output is scripted, nothing is retrieved or indexed |

Because Python is fake, an answer is whatever the spec scripts (`fake.script('chat_stream', ...)`; "Fake answer" by
default). A spec can hold a stream open on a named gate (`held_stream` + `fake.openGate`), park an
`ask_user_question` card, and read every request Node sent Python (`fake.requests`), which is how J-04 proves a
queued message is sent once and J-09 proves no tool ran. The model and its health check are fake too (a model is
seeded so the composer will send). Anything that needs real retrieval, citations or a real LLM does not belong here.

Teardown (`trap` on exit, Ctrl-C and SIGTERM) removes the `pcc-pw-*` container, the Next.js process group, the Node
API, the fake and the `pcc-e2e-*` Mongo/Redis project, and fails loudly if a container of the project is left.

## How it is wired

- `run.sh` starts `python -m helper.collab_stack.serve` (integration-tests): the lane's `CollabStack`, the
  collaboration flag turned on through the admin settings route, a seeded roster, and a small JSON control API on
  127.0.0.1. It writes `state.json` (Node URL, control URL, and per roster user a session JWT). Then it starts
  Next.js, warms `/login/` and `/chat/`, and runs Playwright in Docker with `PCC_STACK_STATE` pointing at that file.
- **Auth**: no login form. The Node API verifies any JWT signed with the lane's secret, so `support/two-users.fixture.ts`
  builds a context per user whose storage state holds `jwt_access_token` / `jwt_refresh_token` (the keys
  `lib/store/auth-store.ts` reads). Two users = two contexts = two separate localStorages.
- **CORS**: the browser opens the app on `http://localhost:<fe port>` and the API on `http://localhost:<node port>`;
  Node is started with `ALLOWED_ORIGINS` for that origin.
- `support/stack.ts`: `fake` (control API client), `NodeApi` (seeding and forced calls as a roster user, over real HTTP),
  `waitFor`. `support/chat-ui.ts`: composer, share drawer and author-chip helpers. `support/a11y.ts`: axe scan that
  fails on serious or critical violations; `KNOWN_EXCEPTIONS` lists the recorded waivers with reasons.
- The `collab-chats` project is in `frontend/playwright.config.ts` (serial, one worker, `--no-sandbox` because the
  image runs the browser as a non-root user). The `authenticated` project ignores this directory.

## Conventions (PH-12)

- Every test is tagged `@collab` and each file is `serial`: one journey, one state. `npx playwright test --grep @collab` picks them.
- `support/two-users.fixture.ts`: `users.a` (owner), `users.b` (editor), `users.c` (viewer), `users.newPerson(key)` for other roster users, and
  `users.fresh(label)` for a new user with their own sharing budget and owner-status memory (the Node API limits a sharer to 20 changes a minute).
  Chats a test creates through `NodeApi` are deleted when it ends; titles start with `e2e-collab-` (`chatTitle()`).
- `support/collab.helper.ts`: `shareChat`, `waitForFeedRev`, `setFlags` (waits until the effective-flags route agrees), `composer`.
- Control API additions for these journeys: `fake.freshActor`, `setDisabled`, `changeTeamMember` (a change outside Node), `cache`/`deleteCache`
  (the decision and team-id caches with their TTL), `serviceToken` (for `pdpAllows`).
- The Python-fake split: anything about what the model does, retrieval, the record routes or the write guard is covered in Python tests; the
  header of each spec names them. These specs assert what Node sends and what the browser shows.
- The default `npm run test:e2e` never lists these specs: the `collab-chats` project exists only when `PCC_STACK_STATE` is set (run.sh).

## Extending (PH-12)

PH-12 PR-12.1 builds on this: `users.a` / `users.b` are the `pageA` / `pageB` of its two-user fixture
(`users.newPerson('read_recipient' | 'stranger' | 'admin')` adds a third context), `NodeApi` is its `apiA` / `apiB`,
and `fake` is the scripting it needs for J-06..J-13. Add journeys as `jNN-<slug>.spec.ts` here; add fake routes to
`fake_backend.py` and new control calls to `helper/collab_stack/serve.py`.
