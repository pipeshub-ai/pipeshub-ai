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
    include: [
      'app/(main)/notifications/__tests__/store.test.ts',
      'app/(main)/notifications/__tests__/useNotificationSocket.test.tsx',
      'app/components/ui/__tests__/help-tooltip.test.ts',
      'app/(main)/workspace/connectors/utils/__tests__/manual-indexing-tooltip.test.ts',
      'lib/socket/__tests__/notification-socket.test.ts',
      'app/(main)/chat/__tests__/agui-event-handler.test.ts',
      'app/(main)/chat/components/message-area/__tests__/agent-activity.test.tsx',
    ],
    passWithNoTests: false,
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, '.'),
    },
  },
});
