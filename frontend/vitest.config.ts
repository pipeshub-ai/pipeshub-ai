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
      'app/(main)/workspace/connectors/utils/__tests__/admin-access-helpers.test.ts',
      'lib/socket/__tests__/notification-socket.test.ts',
      'app/(main)/chat/__tests__/agui-event-handler.test.ts',
      'app/(main)/chat/__tests__/agent-capabilities.test.ts',
      'app/(main)/chat/__tests__/api.test.ts',
      'app/(main)/chat/__tests__/streaming-stop.test.ts',
      'app/(main)/chat/__tests__/reasoning-effort.test.ts',
      'app/(main)/chat/__tests__/attachment-types.test.ts',
      'app/(main)/chat/components/message-area/__tests__/agent-activity.test.tsx',
      'app/(main)/chat/components/message-area/__tests__/expandable-user-query.test.tsx',
      'app/(main)/chat/components/message-area/__tests__/answer-content.test.tsx',
      'app/(main)/chat/utils/__tests__/parse-download-markers.test.ts',
      'app/(main)/chat/utils/__tests__/tool-display.test.ts',
      'app/(main)/chat/utils/__tests__/repair-streaming-markdown.test.ts',
      'app/(main)/chat/utils/__tests__/split-streaming-markdown.test.ts',
      'app/(main)/chat/utils/__tests__/build-chat-artifact.test.ts',
      'app/(main)/chat/utils/__tests__/paste-attachment.test.ts',
      'app/(main)/chat/components/__tests__/pasted-text-chip.test.tsx',
      'app/(main)/chat/components/__tests__/text-preview-dialog.test.tsx',
      'app/(main)/chat/__tests__/project-api.test.ts',
      'app/(main)/chat/__tests__/build-chat-url.test.ts',
      'app/(main)/chat/__tests__/project-store.test.ts',
      'app/(main)/chat/__tests__/project-scope.test.ts',
      'app/(main)/projects/hooks/__tests__/use-kb-stream-upload.test.ts',
      'app/(main)/workspace/skills/personal/__tests__/api.test.ts',
      'app/(main)/workspace/skills/personal/__tests__/skill-availability.test.ts',
      'app/(main)/workspace/components/__tests__/destructive-typed-confirmation-dialog.test.tsx',
      'app/(main)/agents/agent-builder/__tests__/skill-node-warning.test.ts',
      'app/(main)/agents/agent-builder/components/__tests__/flow-node-warning.test.tsx',
      'app/(main)/workspace/mcp-servers/__tests__/oauth-dcr-requirement.test.ts',
      'app/(main)/workspace/ai-models/__tests__/resolve-model-config-save-error.test.ts',
      'app/(main)/onboarding/utils/__tests__/s3-credentials.test.ts',
      'app/(main)/workspace/connectors/components/__tests__/vector-store-actions.test.tsx',
      'app/(public)/oauth/device/__tests__/oauth-device-view.test.tsx',
      'lib/store/__tests__/auth-store.test.ts',
      'lib/store/__tests__/feature-flags-store.test.ts',
      'app/components/ui/__tests__/beta-badge.test.tsx',
      'lib/hooks/__tests__/use-feature-flag-guard.test.ts',
      'app/(main)/workspace/sidebar/__tests__/sidebar-skills-visibility.test.tsx',
      'app/(main)/agents/agent-builder/hooks/__tests__/use-node-templates.test.ts',
    ],
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
