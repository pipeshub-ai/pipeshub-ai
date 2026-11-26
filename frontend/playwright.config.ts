import { defineConfig, devices } from '@playwright/test';
import dotenv from 'dotenv';
import { readFileSync } from 'node:fs';
import path from 'node:path';

import { specSets } from './tests/e2e/support/spec-sets';

dotenv.config({ path: '.env.test' });

const COVERAGE_ENABLED = process.env.COVERAGE === 'true';

// Which project runs which spec, each pattern anchored on the e2e folder (Playwright matches absolute paths).
const SPECS = specSets(path.join(__dirname, 'tests', 'e2e'));
const HARNESS_LANE = process.env.E2E_HARNESS_LANE === '1';

/** How far a UX capture may drift from its baseline before it fails (tests/e2e/qa/ux-captures.json). */
const UX_SCREENSHOT = (() => {
  const manifest = JSON.parse(readFileSync(path.join(__dirname, 'tests', 'e2e', 'qa', 'ux-captures.json'), 'utf8')) as {
    maxDiffPixelRatio: number;
    threshold: number;
  };
  return { maxDiffPixelRatio: manifest.maxDiffPixelRatio, threshold: manifest.threshold, animations: 'disabled' as const, caret: 'hide' as const, scale: 'css' as const };
})();

// No retries: a harness flake is root-caused, never retried away (test plan 00 §5). Traces are kept for failures.
const harnessProject = (name: string, testMatch: RegExp[]) => ({
  name,
  testMatch,
  // No harness spec starts on a stack where a turn could reach a model other than the scripted one.
  dependencies: ['harness-model'],
  retries: 0,
  // Every spec here drives the one scripted model and SaaS of the stack: two workers mix their asks and holds
  // (CJFIX-A found it on CJ-5's specs), so a project runs on one worker whatever the run's worker count.
  workers: 1,
  use: {
    ...devices['Desktop Chrome'],
    storageState: '.auth/user.json',
    trace: 'retain-on-failure' as const,
  },
});

/** A UX capture project: its baselines out of git (PXH_UX_BASELINES), compared with ux-captures.json's tolerance. */
const uxProject = (name: string, testMatch: RegExp[]) => ({
  ...harnessProject(name, testMatch),
  snapshotPathTemplate: `${process.env.PXH_UX_BASELINES || 'tests/e2e/qa/.ux-baselines'}/{arg}{ext}`,
  expect: { toHaveScreenshot: UX_SCREENSHOT },
});

// Sign-up and onboarding need a stack with no organization, so they run only
// when asked for, before CI creates the org every other test signs in to.
const FIRST_RUN = process.env.E2E_FIRST_RUN === 'true';

/**
 * WP-71: a spec tagged `@disposable` changes what it cannot put back (an org's SMTP, people, shared settings), so it
 * runs only on a stack made for the run and torn down after it (deployment/e2e/run.sh sets this). On any other stack
 * it is left out, never run against people's data.
 */
const DISPOSABLE_STACK = process.env.E2E_DISPOSABLE_STACK === '1';
/**
 * A spec tagged `@ht1` reads the fixtures of the human-test stack (scripts/human-test-stack.sh: its accounts, its GitHub
 * connection), so it runs only where those accounts are in the environment.
 */
const HT1_ACCOUNTS = Boolean(process.env.PXH_HT1_ADMIN_EMAIL);
const LEFT_OUT = [...(DISPOSABLE_STACK ? [] : [/@disposable/]), ...(HT1_ACCOUNTS ? [] : [/@ht1/])];

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
  grepInvert: LEFT_OUT.length > 0 ? LEFT_OUT : undefined,
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
      testMatch: SPECS.setup,
    },

    // Seed data — runs after auth, uses saved auth state
    {
      name: 'seed',
      testMatch: SPECS.seed,
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
      testIgnore: SPECS.notAuthenticated,
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
      testMatch: SPECS.responsive,
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
      testMatch: SPECS.ai,
      dependencies: ['setup', 'embedding-model', 'ai-models'],
      use: {
        ...devices['Desktop Chrome'],
        storageState: '.auth/user.json',
      },
    },

    ...(HARNESS_LANE
      ? [
          { name: 'harness-model', testMatch: /setup\/harness-model\.guard\.ts/, dependencies: ['setup'], retries: 0 },
          harnessProject('harness', SPECS.harness),
          harnessProject('harness-engine', SPECS.harnessEngine),
          // QA-CLASSIC: every classic page and key state at desktop and phone width, light and dark, against
          // baselines kept out of git (scripts/qa_full.py --update-ux-baselines makes them; ux-captures.json).
          uxProject('ux-shots', SPECS.qaUx),
          // The engine job's captures, on run.sh durable-up's stack, into the same baselines.
          uxProject('ux-shots-engine', SPECS.qaUxEngine),
          harnessProject('qa-latency', SPECS.qaLatency),
          harnessProject('qa-indexed', SPECS.qaIndexed),
        ]
      : []),

    // A fresh install's first admin; see FIRST_RUN above.
    ...(FIRST_RUN
      ? [{
          name: 'first-run',
          testMatch: SPECS.firstRun,
          // A retry would find the org the first attempt created.
          retries: 0,
          use: { ...devices['Desktop Chrome'] },
        }]
      : []),

    // EV-15 / PF-17: the spec serves every API and its own sign-in; no stack, no saved state.
    {
      name: 'perf',
      testMatch: SPECS.perf,
      retries: 0,
      use: { ...devices['Desktop Chrome'] },
    },

    // Unauthenticated tests — no dependencies, no saved state
    {
      name: 'unauthenticated',
      testMatch: SPECS.login,
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
