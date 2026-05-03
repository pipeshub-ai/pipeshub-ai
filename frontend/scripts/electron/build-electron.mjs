/**
 * Cross-platform Electron desktop build driver (Next static export → electron-builder).
 *
 * Usage (from frontend/):
 *   node scripts/electron/build-electron.mjs mac
 *   node scripts/electron/build-electron.mjs win
 *   node scripts/electron/build-electron.mjs mac-win
 *
 * `mac-win` runs one electron-builder invocation with both targets (usual host: macOS).
 * Build Windows installers on a Windows machine with `win` for best results.
 */

import { spawnSync, execSync } from 'child_process';
import { existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..', '..');

/** Matches electron-builder DMG volume name (productName — see electron-builder.yml). */
const DMG_MOUNT_PATH = '/Volumes/PipesHub';

process.chdir(ROOT);

if (process.env.CSC_IDENTITY_AUTO_DISCOVERY === undefined) {
  process.env.CSC_IDENTITY_AUTO_DISCOVERY = 'false';
}

const shell = process.platform === 'win32';

/**
 * If a previous DMG build crashed or was interrupted, the disk image can stay
 * mounted at /Volumes/PipesHub. The next build then fails with:
 *   hdiutil detach ... Exit code: 16
 * Unmount before packaging so hdiutil can create a fresh DMG.
 */
function unmountStaleDmgVolume() {
  if (process.platform !== 'darwin') return;
  if (!existsSync(DMG_MOUNT_PATH)) return;
  console.warn(
    '\n==> Unmounting stale DMG volume (leftover from a prior build): ' + DMG_MOUNT_PATH + '\n'
  );
  try {
    execSync(`hdiutil detach "${DMG_MOUNT_PATH}" -force`, {
      stdio: 'inherit',
    });
  } catch {
    try {
      execSync(`diskutil unmount force "${DMG_MOUNT_PATH}"`, { stdio: 'inherit' });
    } catch {
      console.error(
        '\nCould not unmount ' +
          DMG_MOUNT_PATH +
          '. Close any Finder window showing that volume, then run:\n' +
          `  hdiutil detach "${DMG_MOUNT_PATH}" -force\n`
      );
    }
  }
}

function run(label, command, args) {
  console.log(`\n==> ${label}\n`);
  const result = spawnSync(command, args, {
    cwd: ROOT,
    stdio: 'inherit',
    shell,
    env: process.env,
  });
  const code = result.status === null ? 1 : result.status;
  if (code !== 0) {
    console.error(`\n==> FAILED: ${label} (exit ${code})\n`);
    process.exit(code);
  }
}

function tryMacDmgRetry() {
  unmountStaleDmgVolume();
  const dirs = ['mac-arm64', 'mac', 'mac-universal'];
  for (const d of dirs) {
    const appPath = join(ROOT, 'dist-electron', d, 'PipesHub.app');
    if (!existsSync(appPath)) continue;
    console.warn(
      `\n==> Retrying DMG only (after packaging succeeded but DMG step failed):\n    ${appPath}\n`
    );
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
      { cwd: ROOT, stdio: 'inherit', shell, env: process.env }
    );
    const code = r.status === null ? 1 : r.status;
    if (code === 0) {
      console.log('\n==> DMG retry succeeded.\n');
      process.exit(0);
    }
  }
}

let mode = (process.argv[2] || '').toLowerCase().trim();
if (mode === 'all') mode = 'mac-win';

if (!['mac', 'win', 'mac-win'].includes(mode)) {
  console.error('Usage: node scripts/electron/build-electron.mjs <mac|win|mac-win|all>');
  console.error('  all — same as mac-win (DMG + NSIS in one run)');
  process.exit(1);
}

run('Next.js static export (ELECTRON_STATIC)', 'npm', ['run', 'build:electron']);
run('Electron prepare (tsc + copy out/ + icon)', 'npm', ['run', 'electron:prepare']);

let ebArgs = ['electron-builder', '--config', 'electron-builder.yml', '--publish', 'never'];
if (mode === 'mac-win') {
  ebArgs = ['electron-builder', '--mac', '--win', '--config', 'electron-builder.yml', '--publish', 'never'];
} else if (mode === 'mac') {
  ebArgs = ['electron-builder', '--mac', '--config', 'electron-builder.yml', '--publish', 'never'];
} else {
  ebArgs = ['electron-builder', '--win', '--config', 'electron-builder.yml', '--publish', 'never'];
}

if (mode === 'mac' || mode === 'mac-win') {
  unmountStaleDmgVolume();
}

console.log(`\n==> electron-builder (${mode})\n`);
const r = spawnSync('npx', ebArgs, {
  cwd: ROOT,
  stdio: 'inherit',
  shell,
  env: process.env,
});

const exitCode = r.status === null ? 1 : r.status;

if (exitCode === 0) {
  process.exit(0);
}

if (mode === 'mac' || mode === 'mac-win') {
  tryMacDmgRetry();
}

process.exit(exitCode);
