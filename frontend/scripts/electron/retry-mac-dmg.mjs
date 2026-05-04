/**
 * Rebuild only the macOS DMG from a packaged app under dist-electron (e.g. mac-arm64).
 * Use when electron:build:mac created the .app but the DMG step failed.
 */
import { existsSync } from 'fs';
import { join, dirname } from 'path';
import { spawnSync } from 'child_process';
import { fileURLToPath } from 'url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const distElectron = join(root, 'dist-electron');
const shell = process.platform === 'win32';

const appPath = ['mac-arm64', 'mac', 'mac-universal']
  .map((d) => join(distElectron, d, 'PipesHub.app'))
  .find((p) => existsSync(p));

if (!appPath) {
  console.error(
    'No dist-electron/mac-*/PipesHub.app found. Run npm run electron:build:mac first.'
  );
  process.exit(1);
}

const r = spawnSync(
  'npx',
  [
    'electron-builder',
    '--mac',
    'dmg',
    '--config',
    'electron-builder.yml',
    '--publish',
    'never',
    '--prepackaged',
    appPath,
  ],
  { cwd: root, stdio: 'inherit', shell, env: process.env }
);
process.exit(r.status === null ? 1 : r.status ?? 1);
