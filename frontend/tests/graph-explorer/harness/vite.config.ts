import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';

const here = path.dirname(fileURLToPath(import.meta.url));
const frontend = path.resolve(here, '../../..');

// The explorer is the product's own code; only what it imports from the rest of the app is replaced.
export default defineConfig({
  root: here,
  oxc: { jsx: { runtime: 'automatic' } },
  resolve: {
    alias: [
      { find: /^@\/lib\/api$/, replacement: path.join(here, 'stubs/api.ts') },
      { find: /^@\/lib\/api\/session-cache$/, replacement: path.join(here, 'stubs/session-cache.ts') },
      { find: /^@\/lib\/i18n$/, replacement: path.join(here, 'stubs/i18n.ts') },
      { find: /^react-i18next$/, replacement: path.join(here, 'stubs/react-i18next.ts') },
      { find: /^next\/navigation$/, replacement: path.join(here, 'stubs/next-navigation.ts') },
      { find: /^@\//, replacement: `${frontend}/` },
    ],
  },
  build: { outDir: path.join(here, 'dist'), emptyOutDir: true },
});
