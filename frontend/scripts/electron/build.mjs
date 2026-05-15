/**
 * Electron desktop build driver.
 *
 * Usage (from frontend/):
 *   node scripts/electron/build.mjs mac   -> dist-electron/mac/  (.dmg)
 *   node scripts/electron/build.mjs win   -> dist-electron/win/  (.exe)
 *   node scripts/electron/build.mjs linux -> dist-electron/linux/
 *   node scripts/electron/build.mjs all   -> builds mac, win, then linux
 *
 * Each run wipes only the requested platform output folders before packaging,
 * so artifacts always reflect the current build.
 */

import { spawnSync, execSync } from 'child_process';
import { existsSync, readdirSync, renameSync, rmSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..', '..');
const DMG_MOUNT_PATH = '/Volumes/PipesHub';
const MAC_ARCHES = ['--x64', '--arm64'];
const WIN_ARCHES = ['--x64', '--arm64'];
const LINUX_ARCHES = ['--x64', '--arm64'];
const PLATFORM_FLAGS = {
  mac: '--mac',
  win: '--win',
  linux: '--linux',
};

process.chdir(ROOT);

if (process.env.CSC_IDENTITY_AUTO_DISCOVERY === undefined) {
  process.env.CSC_IDENTITY_AUTO_DISCOVERY = 'false';
}

const shell = process.platform === 'win32';
const mode = (process.argv[2] || '').toLowerCase().trim();

if (!['mac', 'win', 'linux', 'all'].includes(mode)) {
  console.error('Usage: node scripts/electron/build.mjs <mac|win|linux|all>');
  process.exit(1);
}

const modes = mode === 'all' ? ['mac', 'win', 'linux'] : [mode];

function platformOutDir(targetMode) {
  return join('dist-electron', targetMode);
}

function fail(message) {
  console.error(`\n==> ${message}\n`);
  process.exit(1);
}

function run(label, command, args) {
  console.log(`\n==> ${label}\n`);
  const result = spawnSync(command, args, { cwd: ROOT, stdio: 'inherit', shell, env: process.env });
  const code = result.status === null ? 1 : result.status;
  if (code !== 0) {
    fail(`FAILED: ${label} (exit ${code})`);
  }
}

function commandPath(command) {
  if (process.platform === 'win32') {
    const result = spawnSync('where', [command], { encoding: 'utf8', shell: true });
    return result.status === 0 ? result.stdout.trim().split(/\r?\n/)[0] : null;
  }

  try {
    return execSync(`command -v ${command}`, {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    })
      .trim()
      .split('\n')[0];
  } catch {
    return null;
  }
}

function unique(items) {
  return [...new Set(items.filter(Boolean))];
}

function pyenvPythonCandidates() {
  const pyenv = commandPath('pyenv');
  if (!pyenv) return [];

  try {
    const pyenvRoot = execSync(`${pyenv} root`, {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim();
    const versionsDir = join(pyenvRoot, 'versions');
    if (!existsSync(versionsDir)) return [];

    return readdirSync(versionsDir).flatMap((version) => [
      join(versionsDir, version, 'bin', 'python3'),
      join(versionsDir, version, 'bin', 'python'),
    ]);
  } catch {
    return [];
  }
}

function isWorkingPython(candidate) {
  if (!candidate || !existsSync(candidate)) return false;

  const result = spawnSync(
    candidate,
    [
      '-c',
      'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)',
    ],
    { stdio: 'ignore' },
  );
  return result.status === 0;
}

function configureDmgPython() {
  const candidates = unique([
    process.env.PYTHON_PATH,
    process.env.PYTHON,
    commandPath('python3'),
    commandPath('python'),
    '/opt/homebrew/bin/python3',
    '/usr/local/bin/python3',
    '/usr/bin/python3',
    ...pyenvPythonCandidates(),
  ]);

  const pythonPath = candidates.find(isWorkingPython);
  if (!pythonPath) {
    fail(
      [
        'mac DMG build needs Python 3.8+ for dmg-builder, but no working Python was found.',
        'Install Python 3 or set PYTHON_PATH to a valid interpreter, then retry.',
      ].join('\n'),
    );
  }

  process.env.PYTHON_PATH = pythonPath;
  console.log(`==> Using Python for DMG build: ${pythonPath}`);
}

function preflight() {
  if (modes.includes('mac')) {
    if (process.platform !== 'darwin') {
      fail('mac DMG builds must run on macOS because electron-builder uses hdiutil.');
    }
    if (!commandPath('hdiutil')) {
      fail('mac DMG builds require hdiutil, but it was not found on PATH.');
    }
    configureDmgPython();
  }
}

// A previous interrupted DMG build can leave /Volumes/PipesHub mounted; the
// next hdiutil run then fails with "Resource busy" or detach errors.
function unmountStaleDmgVolume() {
  if (process.platform !== 'darwin' || !existsSync(DMG_MOUNT_PATH)) return;

  console.warn(`\n==> Unmounting stale DMG volume: ${DMG_MOUNT_PATH}\n`);
  for (const command of [
    ['hdiutil', ['detach', '-quiet', DMG_MOUNT_PATH]],
    ['hdiutil', ['detach', '-force', '-quiet', DMG_MOUNT_PATH]],
    ['diskutil', ['unmount', 'force', DMG_MOUNT_PATH]],
  ]) {
    const result = spawnSync(command[0], command[1], { stdio: 'inherit' });
    if (result.status === 0 || !existsSync(DMG_MOUNT_PATH)) return;
  }

  fail(
    [
      `Could not unmount ${DMG_MOUNT_PATH}.`,
      'Close any Finder window on the volume, then run:',
      `  hdiutil detach "${DMG_MOUNT_PATH}" -force`,
    ].join('\n'),
  );
}

function pause(seconds) {
  if (process.platform === 'win32') {
    spawnSync('timeout', ['/t', String(seconds), '/nobreak'], { stdio: 'ignore', shell: true });
    return;
  }

  spawnSync('sleep', [String(seconds)], { stdio: 'ignore' });
}

function buildElectron(targetMode, archFlag, options = {}) {
  const { retryOnDmgFailure = false } = options;
  const outputDir = platformOutDir(targetMode);
  const archLabel = archFlag.replace(/^--/, '');
  const ebArgs = [
    'electron-builder',
    PLATFORM_FLAGS[targetMode],
    '--config',
    'electron-builder.yml',
    `--config.directories.output=${outputDir}`,
    '--publish',
    'never',
    archFlag,
  ];

  const attempts = retryOnDmgFailure ? 2 : 1;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    console.log(`\n==> electron-builder (${targetMode}, ${archLabel}) -> ${outputDir}/\n`);
    const result = spawnSync('npx', ebArgs, { cwd: ROOT, stdio: 'inherit', shell, env: process.env });
    const code = result.status === null ? 1 : result.status ?? 1;

    if (code === 0) return;
    if (attempt === attempts) {
      fail(`FAILED: electron-builder (${targetMode}, ${archLabel}) (exit ${code})`);
    }

    console.warn(`\n==> Retrying ${targetMode} ${archLabel} after DMG cleanup\n`);
    unmountStaleDmgVolume();
    pause(2);
  }
}

function buildMac() {
  // `electron-builder.yml` intentionally lists one `dmg` target without an
  // embedded arch array. Each invocation below is one arch, which avoids two
  // DMG builds fighting over the same /Volumes/PipesHub mount.
  for (const arch of MAC_ARCHES) {
    unmountStaleDmgVolume();
    buildElectron('mac', arch, { retryOnDmgFailure: true });
    unmountStaleDmgVolume();
    pause(2);
  }
}

function buildWin() {
  // Running NSIS per arch avoids an extra combined installer and keeps artifact
  // names explicit: PipesHub-<version>-win-x64.exe and win-arm64.exe.
  for (const arch of WIN_ARCHES) {
    buildElectron('win', arch);
  }
}

function normalizeLinuxArtifactNames(archFlag) {
  if (archFlag !== '--x64') return;

  const outDir = join(ROOT, platformOutDir('linux'));
  if (!existsSync(outDir)) return;

  for (const fileName of readdirSync(outDir)) {
    const normalizedName = fileName
      .replace(/linux-amd64(\.deb)$/, 'linux-x64$1')
      .replace(/linux-x86_64(\.AppImage)$/, 'linux-x64$1');

    if (normalizedName === fileName) continue;

    const source = join(outDir, fileName);
    const target = join(outDir, normalizedName);
    if (existsSync(target)) rmSync(target, { force: true });
    renameSync(source, target);
    console.log(`==> Renamed Linux artifact: ${fileName} -> ${normalizedName}`);
  }
}

function buildLinux() {
  // Linux targets are configured once in electron-builder.yml; arch stays a
  // script concern so AppImage/deb artifact names remain predictable.
  for (const arch of LINUX_ARCHES) {
    buildElectron('linux', arch);
    // electron-builder uses distro-native names for x64 Linux artifacts:
    // deb -> amd64, AppImage -> x86_64. Keep package metadata intact but make
    // public release filenames match the mac/win x64 naming.
    normalizeLinuxArtifactNames(arch);
  }
}

preflight();

for (const targetMode of modes) {
  const outDir = platformOutDir(targetMode);
  const outAbs = join(ROOT, outDir);
  if (existsSync(outAbs)) {
    console.log(`\n==> Cleaning ${outDir}/\n`);
    rmSync(outAbs, { recursive: true, force: true });
  }
}

run('Next.js static export', 'npm', ['run', 'build:electron']);
run('Electron prepare (tsc + copy out/ + icons)', 'npm', ['run', 'electron:prepare']);

for (const targetMode of modes) {
  if (targetMode === 'mac') buildMac();
  if (targetMode === 'win') buildWin();
  if (targetMode === 'linux') buildLinux();
}
