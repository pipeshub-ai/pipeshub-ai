/**
 * Electron desktop build driver.
 *
 * Usage (from frontend/):
 *   node scripts/electron/build.mjs mac   → dist-electron/mac/  (.dmg)
 *   node scripts/electron/build.mjs win   → dist-electron/win/  (.exe)
 *
 * Each run wipes its own platform subfolder before packaging, so artifacts
 * always reflect the current build. The other platform's folder is left in
 * place — to rebuild both, run the mac command then the win command.
 */

import { spawnSync, execSync } from 'child_process';
import { existsSync, rmSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..', '..');
const DMG_MOUNT_PATH = '/Volumes/PipesHub';

process.chdir(ROOT);

if (process.env.CSC_IDENTITY_AUTO_DISCOVERY === undefined) {
  process.env.CSC_IDENTITY_AUTO_DISCOVERY = 'false';
}

const shell = process.platform === 'win32';

const mode = (process.argv[2] || '').toLowerCase().trim();
if (!['mac', 'win'].includes(mode)) {
  console.error('Usage: node scripts/electron/build.mjs <mac|win>');
  process.exit(1);
}

const platformOutDir = join('dist-electron', mode);
const platformOutAbs = join(ROOT, platformOutDir);

if (existsSync(platformOutAbs)) {
  console.log(`\n==> Cleaning ${platformOutDir}/\n`);
  rmSync(platformOutAbs, { recursive: true, force: true });
}

// A previous interrupted DMG build can leave /Volumes/PipesHub mounted; the
// next hdiutil run then fails with "Resource busy" (exit 16).
function unmountStaleDmgVolume() {
  if (process.platform !== 'darwin') return;
  if (!existsSync(DMG_MOUNT_PATH)) return;
  console.warn(`\n==> Unmounting stale DMG volume: ${DMG_MOUNT_PATH}\n`);
  try {
    execSync(`hdiutil detach "${DMG_MOUNT_PATH}" -force`, { stdio: 'inherit' });
  } catch {
    try {
      execSync(`diskutil unmount force "${DMG_MOUNT_PATH}"`, { stdio: 'inherit' });
    } catch {
      console.error(
        `Could not unmount ${DMG_MOUNT_PATH}. Close any Finder window on the volume, then run:\n  hdiutil detach "${DMG_MOUNT_PATH}" -force\n`,
      );
    }
  }
}

function run(label, command, args) {
  console.log(`\n==> ${label}\n`);
  const result = spawnSync(command, args, { cwd: ROOT, stdio: 'inherit', shell, env: process.env });
  const code = result.status === null ? 1 : result.status;
  if (code !== 0) {
    console.error(`\n==> FAILED: ${label} (exit ${code})\n`);
    process.exit(code);
  }
}

run('Next.js static export', 'npm', ['run', 'build:electron']);
run('Electron prepare (tsc + copy out/ + icons)', 'npm', ['run', 'electron:prepare']);

function buildElectron(archFlag) {
  const archLabel = archFlag ? archFlag.replace(/^--/, '') : 'all';
  const ebArgs = [
    'electron-builder',
    mode === 'mac' ? '--mac' : '--win',
    '--config',
    'electron-builder.yml',
    `--config.directories.output=${platformOutDir}`,
    '--publish',
    'never',
  ];
  if (archFlag) ebArgs.push(archFlag);

  console.log(`\n==> electron-builder (${mode}, ${archLabel}) → ${platformOutDir}/\n`);
  const r = spawnSync('npx', ebArgs, { cwd: ROOT, stdio: 'inherit', shell, env: process.env });
  const code = r.status === null ? 1 : r.status ?? 1;
  if (code !== 0) {
    console.error(`\n==> FAILED: electron-builder (${mode}, ${archLabel}) (exit ${code})\n`);
    process.exit(code);
  }
}

if (mode === 'mac') {
  // Both archs use the same `dmg.title` so both DMG builds want to mount at
  // `/Volumes/PipesHub`. electron-builder doesn't detach the volume between
  // archs when invoked once for both, so the second mount inherits the first's
  // stale Applications symlink and `ln -s /Applications` fails with
  // "File exists". Run each arch in its own invocation, unmounting between.
  for (const arch of ['--x64', '--arm64']) {
    unmountStaleDmgVolume();
    buildElectron(arch);
    unmountStaleDmgVolume();
  }
} else {
  // When `arch: [x64, arm64]` is in a single NSIS target, electron-builder
  // emits an extra ~175 MB combined `PipesHub-${version}-win.exe` alongside
  // the per-arch installers. Splitting the run per-arch keeps each invocation
  // unaware of the other so only the two arch-specific installers are written.
  for (const arch of ['--x64', '--arm64']) {
    buildElectron(arch);
  }
}
