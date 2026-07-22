import { defineConfig } from 'vitest/config';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  test: {
    environment: 'jsdom',
    globals: false,
    include: [
      'app/(main)/notifications/__tests__/store.test.ts',
      'app/(main)/notifications/__tests__/useNotificationSocket.test.tsx',
      'app/components/ui/__tests__/help-tooltip.test.ts',
      'app/(main)/workspace/connectors/utils/__tests__/manual-indexing-tooltip.test.ts',
      'app/(main)/workspace/connectors/utils/__tests__/sync-progress-view.test.ts',
      'app/(main)/workspace/connectors/utils/__tests__/sync-failure-copy.test.ts',
      'app/(main)/workspace/connectors/utils/__tests__/indexing-queue-copy.test.ts',
      'app/(main)/workspace/connectors/utils/__tests__/sync-conflict-copy.test.ts',
      'lib/socket/__tests__/notification-socket.test.ts',
      'app/(main)/knowledge-base/utils/__tests__/indexing-progress.test.ts',
      'app/(main)/knowledge-base/utils/__tests__/kb-table-item-actions.test.ts',
    ],
    passWithNoTests: false,
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, '.'),
    },
  },
});
