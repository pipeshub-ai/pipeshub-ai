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

async function afterPack(context) {
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
      'DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"',
      `exec "$DIR/${realExecutableName}" --no-sandbox "$@"`,
      '',
    ].join('\n'),
    { mode: 0o755 },
  );

  console.log(`[afterPack] Added Linux no-sandbox launcher: ${executableName} -> ${realExecutableName}`);
}

module.exports = afterPack;
module.exports.default = afterPack;
