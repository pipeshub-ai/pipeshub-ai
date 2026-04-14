/**
 * Runs `next build` with static export enabled (required for the Electron shell).
 * See next.config.mjs: ELECTRON_STATIC_EXPORT=1 sets output: 'export'.
 */

import { spawnSync } from 'node:child_process';

process.env.ELECTRON_STATIC_EXPORT = '1';

const result = spawnSync('npx', ['next', 'build'], {
  stdio: 'inherit',
  shell: true,
  env: { ...process.env, ELECTRON_STATIC_EXPORT: '1' },
});

process.exit(result.status ?? 1);
