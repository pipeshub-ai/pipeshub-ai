import { defineConfig, devices } from '@playwright/test';

// Self-contained: pages are served through page.route, so this needs no
// PipesHub server, no login and no .env. Chromium only, because text-fragment
// support (and its ::target-text styling) is what is being verified.
export default defineConfig({
  testDir: './tests/text-fragments',
  testMatch: /.*\.spec\.ts/,
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  timeout: 30_000,
  reporter: process.env.CI ? [['list'], ['junit', { outputFile: 'test-results/text-fragments-junit.xml' }]] : [['list']],
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
