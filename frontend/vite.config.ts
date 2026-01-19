import path from 'path';
import checker from 'vite-plugin-checker';
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react-swc';

// ----------------------------------------------------------------------

const PORT = 3001;

export default defineConfig(({ mode }) => {
  // Load env file based on mode
  const env = loadEnv(mode, process.cwd(), '');

  return {
    plugins: [
      react(),
      checker({
        typescript: true,
        eslint: {
          lintCommand: 'eslint "./src/**/*.{js,jsx,ts,tsx}"',
          dev: { logLevel: ['error'] },
        },
        overlay: {
          position: 'tl',
          initialIsOpen: false,
        },
      }),
    ],
    resolve: {
      alias: [
        {
          find: /^~(.+)/,
          replacement: path.join(process.cwd(), 'node_modules/$1'),
        },
        {
          find: /^src(.+)/,
          replacement: path.join(process.cwd(), 'src/$1'),
        },
      ],
    },
    server: { port: PORT, host: true },
    preview: { port: PORT, host: true },

    // Explicitly define whitelabel env variables (optional but recommended)
    define: {
      'import.meta.env.VITE_APP_NAME': JSON.stringify(env.VITE_APP_NAME ?? ''),
      'import.meta.env.VITE_APP_TITLE': JSON.stringify(env.VITE_APP_TITLE ?? ''),
      'import.meta.env.VITE_APP_TAGLINE': JSON.stringify(env.VITE_APP_TAGLINE ?? ''),
      'import.meta.env.VITE_GITHUB_URL': JSON.stringify(env.VITE_GITHUB_URL ?? ''),
      'import.meta.env.VITE_DOCS_BASE_URL': JSON.stringify(env.VITE_DOCS_BASE_URL ?? ''),
      'import.meta.env.VITE_SIGNIN_IMAGE_URL': JSON.stringify(env.VITE_SIGNIN_IMAGE_URL ?? ''),
      'import.meta.env.VITE_ASSISTANT_NAME': JSON.stringify(env.VITE_ASSISTANT_NAME ?? ''),
    },
  };
});
