import { defineConfig } from 'vitest/config';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  // tsconfig.json's `jsx: "preserve"` (Next.js's own build handles the
  // transform) leaves raw JSX in place for Vite 8's default oxc
  // transformer, which then fails to parse it — override just for the
  // Vitest/oxc pipeline so `.tsx` component tests can render real JSX
  // without adding a new toolchain dependency (no `@vitejs/plugin-react`
  // needed: oxc's built-in JSX transform is enough for plain React
  // component tests).
  oxc: {
    jsx: { runtime: 'automatic' },
  },
  test: {
    environment: 'jsdom',
    globals: false,
    // Every unit test under app/ and lib/. A hand-kept list let new test
    // files sit unrun: three never ran, and one of them caught a real bug.
    // Playwright (tests/e2e) and Electron (electron/, run by
    // test:electron:local-sync) have their own runners.
    include: ['app/**/*.test.{ts,tsx}', 'lib/**/*.test.{ts,tsx}'],
    passWithNoTests: false,
  },
  resolve: {
    alias: {
      '@/chat': path.resolve(__dirname, './app/(main)/chat'),
      '@/knowledge-base': path.resolve(__dirname, './app/(main)/knowledge-base'),
      '@/workspace': path.resolve(__dirname, './app/(main)/workspace'),
      '@': path.resolve(__dirname, '.'),
    },
  },
});
