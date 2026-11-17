const fs = require('fs/promises');
const path = require('path');

async function pathExists(filePath) {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

// Mach-O magics (thin 32/64-bit, both byte orders) and fat binaries.
const MACHO_MAGICS = new Set(['feedface', 'feedfacf', 'cefaedfe', 'cffaedfe', 'cafebabe', 'bebafeca']);

async function isMachO(file) {
  const handle = await fs.open(file, 'r');
  try {
    const buf = Buffer.alloc(4);
    const { bytesRead } = await handle.read(buf, 0, 4, 0);
    return bytesRead === 4 && MACHO_MAGICS.has(buf.toString('hex'));
  } finally {
    await handle.close();
  }
}

/** Every Mach-O file under `dir` (symlinks are not followed or signed twice). */
async function findMachOFiles(dir) {
  const found = [];
  for (const entry of await fs.readdir(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) found.push(...(await findMachOFiles(full)));
    else if (entry.isFile() && (await isMachO(full))) found.push(full);
  }
  return found;
}

/**
 * The launcher bundle under Resources (Node, PostgreSQL, Python and
 * every .so/.dylib in it) is not part of the Electron app proper. List its
 * Mach-O files as extra binaries so electron-builder signs each one with the
 * hardened runtime and the entitlements (a no-op when the build is unsigned).
 */
async function addBundleBinariesToSigning(context) {
  const bundle = path.join(
    context.appOutDir,
    `${context.packager.appInfo.productFilename}.app`,
    'Contents',
    'Resources',
    'pipeshub-native',
  );
  if (!(await pathExists(bundle))) return 0;
  const files = await findMachOFiles(bundle);
  const options = context.packager.platformSpecificBuildOptions;
  options.binaries = [...(options.binaries || []), ...files];
  console.log(`[afterPack] ${files.length} Mach-O files in the launcher bundle added to signing`);
  return files.length;
}

async function afterPack(context) {
  if (context.electronPlatformName === 'darwin') {
    await addBundleBinariesToSigning(context);
    return;
  }
  if (context.electronPlatformName !== 'linux') return;

  const executableName = context.packager.executableName;
  const executablePath = path.join(context.appOutDir, executableName);
  const realExecutableName = `${executableName}-bin`;
  const realExecutablePath = path.join(context.appOutDir, realExecutableName);

  if (await pathExists(realExecutablePath)) return;

  await fs.rename(executablePath, realExecutablePath);
  await fs.chmod(realExecutablePath, 0o755);
  await fs.writeFile(
    executablePath,
    [
      '#!/bin/sh',
      'set -e',
      '# Resolve symlinks first: the deb installs this launcher behind',
      '# /usr/bin/<name> via update-alternatives, so $0 is the symlink, not the',
      '# real app dir. Without this, dirname "$0" points at /usr/bin.',
      'SELF="$(readlink -f -- "$0" 2>/dev/null || echo "$0")"',
      'DIR="$(CDPATH= cd -- "$(dirname -- "$SELF")" && pwd)"',
      `exec "$DIR/${realExecutableName}" --no-sandbox "$@"`,
      '',
    ].join('\n'),
    { mode: 0o755 },
  );

  console.log(`[afterPack] Added Linux no-sandbox launcher: ${executableName} -> ${realExecutableName}`);
}

module.exports = afterPack;
module.exports.default = afterPack;
module.exports.findMachOFiles = findMachOFiles;
module.exports.addBundleBinariesToSigning = addBundleBinariesToSigning;
