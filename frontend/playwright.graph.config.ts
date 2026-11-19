import { defineConfig, devices } from '@playwright/test';

/**
 * Browser tests of the graph explorer against an in-browser fake backend: no stack, no login.
 * See tests/graph-explorer/README.md. Software drawing is fine here (no GPU needed in CI).
 */
const PORT = 4400;

export default defineConfig({
  testDir: './tests/graph-explorer/specs',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  timeout: 60_000,
  reporter: process.env.CI ? [['list'], ['junit', { outputFile: 'test-results/graph-explorer-junit.xml' }]] : [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    actionTimeout: 10_000,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: `npx vite build -c tests/graph-explorer/harness/vite.config.ts && npx vite preview -c tests/graph-explorer/harness/vite.config.ts --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
