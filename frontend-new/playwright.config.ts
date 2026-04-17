import { defineConfig, devices } from '@playwright/test';
import dotenv from 'dotenv';

dotenv.config({ path: '.env.test' });

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [['html', { open: 'never' }]],

  use: {
    baseURL: process.env.BASE_URL || 'http://localhost:5005',
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
    url: 'http://localhost:5005',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
