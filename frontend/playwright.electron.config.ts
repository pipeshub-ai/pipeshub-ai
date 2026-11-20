import { defineConfig } from '@playwright/test';

/**
 * Desktop app e2e: drives the real Electron main process with the stub launcher
 * (electron/__tests__/stub-launcher.ts) in place of the native bundle.
 * Needs `npm run build:electron && npm run electron:prepare` first (the static
 * export in electron/out); on Linux run under xvfb-run.
 */
export default defineConfig({
  testDir: './tests/electron',
  timeout: 120_000,
  workers: 1,
  retries: 0,
  reporter: [['list']],
});
