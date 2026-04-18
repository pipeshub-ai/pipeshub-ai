# Frontend E2E Tests (Playwright)

End-to-end tests for the PipesHub frontend using [Playwright](https://playwright.dev/). Tests cover authentication, navigation, workspace settings, entity CRUD (users, groups, teams), chat, and knowledge base pages.

## Prerequisites

1. Install dependencies:
   ```bash
   cd frontend-new
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
   | `BASE_URL` | Dev server URL (default `http://localhost:5005`) |

## Running Tests

| Command | Description |
|---------|-------------|
| `npm run test:e2e` | Run all tests (starts dev server automatically) |
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

Playwright is configured with four projects that run in order:

1. **setup** — Logs in via the browser and saves auth state to `.auth/user.json`.
2. **seed** — Seeds bulk data using UI interactions + API calls. Depends on `setup`.
3. **authenticated** — All feature tests using saved auth state. Depends on `setup`.
4. **unauthenticated** — Login page tests that run without saved auth.

## Directory Structure

```
e2e/
├── setup/           # Auth setup (login + save storageState)
├── fixtures/        # Shared test fixtures (API context, base)
├── helpers/         # Reusable interaction helpers
│   ├── login.helper.ts
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
├── chat/            # Chat interface tests
└── knowledge-base/  # Knowledge base tests
```

## Writing New Tests

- **Authenticated tests** go in a feature folder under `e2e/` and import from `@playwright/test`. They automatically use the saved auth state.
- **API-based tests** (seeding, cleanup) import from `e2e/fixtures/api-context.fixture.ts` for a pre-authenticated `APIRequestContext`.
- **Helpers** in `e2e/helpers/` provide reusable functions for common UI interactions.

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
