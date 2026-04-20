/**
 * Runs `next build` with ELECTRON_STATIC=1 so next.config.mjs enables
 * `output: 'export'` and emits `out/` for electron-prepare.mjs.
 * (ELECTRON_STATIC_EXPORT is set for compatibility with older configs.)
 */
import { spawnSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, '..');

const result = spawnSync('npx', ['next', 'build'], {
  cwd: root,
  stdio: 'inherit',
  env: {
    ...process.env,
    ELECTRON_STATIC: '1',
    ELECTRON_STATIC_EXPORT: '1',
  },
  shell: process.platform === 'win32',
});

process.exit(result.status === null ? 1 : result.status ?? 1);
