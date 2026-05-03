import { defineConfig, devices } from '@playwright/test';
import dotenv from 'dotenv';

dotenv.config({ path: '.env.test' });

const COVERAGE_ENABLED = process.env.COVERAGE === 'true';

const defaultReporter: any[] = [['html', { open: 'never' }]];
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
  reporter: COVERAGE_ENABLED ? [...defaultReporter, ...coverageReporter] : defaultReporter,

  use: {
    baseURL: process.env.BASE_URL || 'http://localhost:3001',
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
      dependencies: ['setup'],
      use: {
        ...devices['Desktop Chrome'],
        storageState: '.auth/user.json',
      },
    },

    // Authenticated tests — depend on auth setup
    {
      name: 'authenticated',
      testMatch: /.*\.spec\.ts/,
      testIgnore: [/auth\/login\.spec\.ts/, /setup\//, /seed\//],
      dependencies: ['setup'],
      use: {
        ...devices['Desktop Chrome'],
        storageState: '.auth/user.json',
      },
    },

    // Unauthenticated tests — no dependencies, no saved state
    {
      name: 'unauthenticated',
      testMatch: /auth\/login\.spec\.ts/,
      use: {
        ...devices['Desktop Chrome'],
      },
    },
  ],

  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:3001',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
