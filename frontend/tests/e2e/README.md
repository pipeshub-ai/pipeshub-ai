# Frontend E2E Tests (Playwright)

End-to-end tests for the PipesHub frontend using [Playwright](https://playwright.dev/). Tests cover authentication, navigation, workspace settings, entity CRUD (users, groups, teams), chat, and knowledge base pages.

## Prerequisites

1. Install dependencies:
   ```bash
   cd frontend
   npm install
   ```

2. Install Playwright browsers:
   ```bash
   npx playwright install chromium
   ```

3. Create a `.env.test` file from the template:
   ```bash
   cp .env.test.example .env.test
   ```

   Required variables:
   | Variable | Description |
   |----------|-------------|
   | `TEST_USER_EMAIL` | Email of an existing admin user |
   | `TEST_USER_PASSWORD` | Password for that user |
   | `BASE_URL` | Where Playwright opens the app (default in config: `http://localhost:3001`) |
   | `NEXT_PUBLIC_API_BASE_URL` | Backend URL for API calls (seeding/fixtures); defaults to `http://localhost:3000` in fixtures when unset |

   Optional, for the flows that need them (each skips with the reason when its prerequisite is missing):
   | Variable | Description |
   |----------|-------------|
   | `SMTP_HOST` / `SMTP_PORT` | Where the org sends mail. Invite tests configure SMTP from these; CI points them at the stack's Mailpit |
   | `MAILPIT_URL` | Mailpit's web API, where invite tests read the email back (default `http://localhost:8025`) |
   | `TEST_OPENAI_API_KEY` | Lets chat and agent tests add a real model when none is configured (the CI secret; `TEST_OPENAI_LLM_MODEL` / `TEST_OPENAI_EMBEDDING_MODEL` override the models) |
   | `E2E_AI_ENDPOINT` | Instead of OpenAI, any OpenAI-compatible server (for example a local model); `E2E_AI_API_KEY` is its key if it needs one, and `E2E_AI_LLM_MODEL` / `E2E_AI_EMBEDDING_MODEL` name its models |

## Running Tests

| Command | Description |
|---------|-------------|
| `npm run test:e2e` | Run all tests (starts dev server automatically) |
| `npm run test:e2e:smoke` | Run only the `@smoke` tests: the few most important flows, in a few minutes |
| `npm run test:e2e:ui` | Open Playwright UI for interactive debugging |
| `npm run test:e2e:headed` | Run tests in a visible browser |
| `npm run test:e2e:seed` | Seed bulk test data (30 users, 30 groups, 30 teams) |
| `npm run test:e2e:cleanup` | Delete all seeded test data |
| `npm run test:e2e:users` | Run only user-related tests |
| `npm run test:e2e:groups` | Run only group-related tests |
| `npm run test:e2e:teams` | Run only team-related tests |
| `npm run test:e2e:report` | Open the HTML test report |
| `npm run test:e2e:coverage` | Run all tests with V8 code coverage |
| `npm run test:e2e:coverage-report` | Open the coverage HTML report |

## Smoke tests

Tests whose title ends in `@smoke` form a quick set covering the most important flows: signing in, pages loading, a chat answer, a knowledge-base upload, a teammate accepting an invite, and the users, settings and service-health pages. CI runs them on every pull request in their own workflow (`.github/workflows/e2e-smoke.yml`), so a pull request gets a browser signal in minutes rather than after the full integration run. That workflow uses no repository secrets: it starts a throwaway stack and makes up its own admin login for each run. It skips pull requests opened from forks, because it runs on our self-hosted runner. The sign-in setup test is tagged too, because filtering by title would otherwise skip it. Keep the set small and fast; tag a test only if a failure there would block a release.

A test should fail, not skip, when something it needs is missing from the page. Skip only for a genuine environment limit (for example, SMTP not configured, or an Enterprise-only feature), and give the reason.

## Code Coverage

Run `npm run test:e2e:coverage` to collect V8 code coverage during test execution. This uses [monocart-reporter](https://github.com/nicolo-ribaudo/monocart-reporter) to generate coverage reports.

Reports are written to `coverage/e2e/` and include:
- **V8 report** — native V8 coverage with source-mapped file breakdown
- **LCOV** — for CI integration (Codecov, Coveralls, etc.)
- **Console summary** — printed to terminal after the run

Open the HTML report:
```bash
npm run test:e2e:coverage-report
```

## Debugging & Verbose Output

```bash
# Visible browser + trace for every test
npx playwright test --headed --trace on

# Slow motion — 1 second pause between each action
npx playwright test --headed --trace on --slow-mo=1000

# Record video of every test
npx playwright test --headed --video on

# Screenshot after every test (pass or fail)
npx playwright test --screenshot on
```

| Flag | What it does |
|------|-------------|
| `--headed` | Opens a visible browser window instead of running headless |
| `--trace on` | Records a trace for every test (default only records on first retry) |
| `--slow-mo=N` | Adds N milliseconds pause between each Playwright action |
| `--video on` | Records a video of every test run |
| `--screenshot on` | Takes a screenshot after every test (not just failures) |

**Interactive UI mode** (recommended for debugging):
```bash
npm run test:e2e:ui
```

**Viewing traces and reports:**
```bash
npx playwright show-report
npx playwright show-trace test-results/<test-folder>/trace.zip
```

## Test Projects

Playwright is configured with these projects:

1. **setup** — Logs in via the browser and saves auth state to `.auth/user.json`.
2. **seed** — Seeds bulk data using UI interactions + API calls. Depends on `setup`.
3. **authenticated** — All feature tests using saved auth state. Depends on `setup`.
4. **ai** — Tests that need a real AI model (the cited answer and agent tests). They depend on **ai-models**, which adds a chat and an embedding model once if the org has none, and whose teardown (**ai-models-cleanup**) removes them after every `ai` test has finished. Without credentials the models aren't added and these tests skip with the reason.
5. **unauthenticated** — Login page tests that run without saved auth.

## Directory Structure

```
tests/e2e/          # Playwright testDir (repo path: frontend/tests/e2e)
├── setup/           # Auth setup (login + save storageState)
├── fixtures/        # Shared test fixtures (API context, base)
├── helpers/         # Reusable interaction helpers
│   ├── ai-models.helper.ts   # a real chat + embedding model for answering tests
│   ├── login.helper.ts
│   ├── mailpit.helper.ts     # reads invite emails back from Mailpit
│   ├── members.helper.ts     # invite, accept and sign in as a second user
│   ├── entity-table.helper.ts
│   ├── pagination.helper.ts
│   ├── search.helper.ts
│   ├── sidebar-form.helper.ts
│   └── tag-input.helper.ts
├── seed/            # Data seeding and cleanup
├── auth/            # Login and logout tests
├── navigation/      # Routing and sidebar navigation tests
├── workspace/       # Workspace settings page tests
├── users/           # Users table, invite, actions, bulk ops
├── groups/          # Groups table, create, actions
├── teams/           # Teams table, create, actions
├── chat/            # Chat interface tests, including a real cited answer
├── agents/          # Building an agent and chatting with it
└── knowledge-base/  # Knowledge base tests
```

## Writing New Tests

- **Authenticated tests** go in a feature folder under `tests/e2e/` (relative to `frontend/`) and import from `@playwright/test`. They automatically use the saved auth state.
- **API-based tests** (seeding, cleanup) import from `../fixtures/api-context.fixture.ts` relative to specs in sibling folders for a pre-authenticated `APIRequestContext`.
- **Helpers** in `tests/e2e/helpers/` provide reusable functions for common UI interactions.

Example:
```typescript
import { test, expect } from '@playwright/test';

test.describe('My Feature', () => {
  test('loads the page', async ({ page }) => {
    await page.goto('/workspace/my-feature/');
    await expect(page.locator('text="My Feature"')).toBeVisible();
  });
});
```

## Seed Data Conventions

- Users: `e2e-user-XXXX@e2etest.pipeshub.local`
- Groups: `E2E Group XXX`
- Teams: `E2E Team XXXX`
- Always run `npm run test:e2e:cleanup` after seeded test runs

## CI

Set `CI=true` to enable retries (2 attempts), single worker, and fresh dev server.

## Artifacts (gitignored)

- `.auth/` — Saved browser auth state
- `test-results/` — Screenshots, traces
- `playwright-report/` — HTML report
