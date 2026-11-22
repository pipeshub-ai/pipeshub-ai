import { defineConfig, devices } from '@playwright/test';
import dotenv from 'dotenv';

dotenv.config({ path: '.env.test' });

const COVERAGE_ENABLED = process.env.COVERAGE === 'true';

// Tests that need a real AI model; they run in the `ai` project below.
const AI_SPECS = [/chat\/chat-cited-answer\.spec\.ts/, /chat\/chat-link-labels\.spec\.ts/, /agents\/.*\.spec\.ts/];

// Sign-up and onboarding need a stack with no organization, so they run only
// when asked for, before CI creates the org every other test signs in to.
const FIRST_RUN = process.env.E2E_FIRST_RUN === 'true';

const defaultReporter: any[] = [['html', { open: 'never' }]];
const ciReporter: any[] = process.env.CI
  ? [['junit', { outputFile: 'test-results/playwright-junit.xml' }]]
  : [];
const coverageReporter: any[] = [
  ['monocart-reporter', {
    name: 'E2E Coverage Report',
    outputFile: 'coverage/e2e/report.html',
    coverage: {
      reports: ['v8', 'console-details', 'lcov'],
      outputDir: 'coverage/e2e',
      entryFilter: (entry: { url: string }) => {
        const baseUrl = process.env.BASE_URL || 'http://localhost:5005';
        const hostname = new URL(baseUrl).hostname;
        return entry.url.includes(hostname);
      },
      sourceFilter: (sourcePath: string) =>
        sourcePath.includes('app/') && !sourcePath.includes('node_modules'),
    },
  }],
];

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [...defaultReporter, ...ciReporter, ...(COVERAGE_ENABLED ? coverageReporter : [])],

  // Dev server (Turbopack) compiles routes on-demand — the first navigation
  // per route can take 30–60 s. Production build+start is instant.
  // Both cases are covered by these generous budgets.
  timeout: 90_000,

  use: {
    baseURL: process.env.BASE_URL || 'http://localhost:3001',
    // 60 s covers cold Turbopack compilation on first page.goto per route.
    navigationTimeout: 60_000,
    // Individual actions (click, fill, waitForSelector …) get 15 s each.
    actionTimeout: 15_000,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },

  projects: [
    // Auth setup — runs first, saves storageState
    {
      name: 'setup',
      testMatch: /setup\/.*\.setup\.ts/,
    },

    // Seed data — runs after auth, uses saved auth state
    {
      name: 'seed',
      testMatch: /seed\/.*\.spec\.ts/,
      dependencies: ['setup', 'embedding-model'],
      use: {
        ...devices['Desktop Chrome'],
        storageState: '.auth/user.json',
      },
    },

    // Authenticated tests — depend on auth setup
    {
      name: 'authenticated',
      testMatch: /.*\.spec\.ts/,
      testIgnore: [/auth\/login\.spec\.ts/, /setup\//, /seed\//, /first-run\//, /responsive\//, ...AI_SPECS],
      dependencies: ['setup', 'embedding-model'],
      use: {
        ...devices['Desktop Chrome'],
        storageState: '.auth/user.json',
      },
    },

    // Every screen at phone, tablet and desktop sizes. Each test sets its own
    // viewport, so no device preset here.
    {
      name: 'responsive',
      testMatch: /responsive\/.*\.spec\.ts/,
      dependencies: ['setup', 'embedding-model'],
      use: {
        storageState: '.auth/user.json',
        // Its own screenshots go to EVIDENCE_DIR.
        screenshot: 'off',
        trace: 'off',
      },
    },

    // The answering tests' embedding model, added before any project that
    // uploads files: once the built-in model has embedded a record, the backend
    // refuses to switch embedding models.
    {
      name: 'embedding-model',
      testMatch: /setup\/embedding-model\.provision\.ts/,
      dependencies: ['setup'],
      teardown: 'embedding-model-cleanup',
    },
    {
      name: 'embedding-model-cleanup',
      testMatch: /setup\/embedding-model\.cleanup\.ts/,
    },

    // Adds the AI models the answering tests need once, and removes them once
    // after all of them finish, so no test deletes a model another is using.
    {
      name: 'ai-models',
      testMatch: /setup\/ai-models\.provision\.ts/,
      dependencies: ['setup', 'embedding-model'],
      teardown: 'ai-models-cleanup',
    },
    {
      name: 'ai-models-cleanup',
      testMatch: /setup\/ai-models\.cleanup\.ts/,
    },
    {
      name: 'ai',
      testMatch: AI_SPECS,
      dependencies: ['setup', 'embedding-model', 'ai-models'],
      use: {
        ...devices['Desktop Chrome'],
        storageState: '.auth/user.json',
      },
    },

    // A fresh install's first admin; see FIRST_RUN above.
    ...(FIRST_RUN
      ? [{
          name: 'first-run',
          testMatch: /first-run\/.*\.spec\.ts/,
          // A retry would find the org the first attempt created.
          retries: 0,
          use: { ...devices['Desktop Chrome'] },
        }]
      : []),

    // Unauthenticated tests — no dependencies, no saved state
    {
      name: 'unauthenticated',
      testMatch: /auth\/login\.spec\.ts/,
      use: {
        ...devices['Desktop Chrome'],
      },
    },
  ],

  webServer: process.env.PLAYWRIGHT_NO_SERVER ? undefined : {
    // Build once then serve the pre-compiled bundle.
    // - `next build` compiles everything upfront (no on-demand compilation).
    // - `next start` serves the bundle instantly — page.goto is never slow.
    // - `reuseExistingServer` skips the build when a server is already up,
    //   so repeated local runs are fast (only the first run builds).
    command: 'npm run build && npm run start',
    url: 'http://localhost:3001',
    reuseExistingServer: !process.env.CI,
    // Allow up to 5 minutes for the production build to complete on first run.
    timeout: 300_000,
    // Forward NEXT_PUBLIC_* vars so the build bakes the correct API base URL.
    env: {
      NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:3000',
    },
  },
});
