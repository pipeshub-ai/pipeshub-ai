/**
 * Stages the native launcher bundle ("run PipesHub on this computer") into
 * resources/pipeshub-native, which electron-builder.yml ships as extraResources.
 *
 *   PIPESHUB_NATIVE_BUNDLE=/path/to/bundle   copy a folder (launcher + pg/ py/ app/ node/ ...)
 *   PIPESHUB_NATIVE_BUNDLE_URL=https://...   download a .tar.gz and unpack it
 *   (neither)                                stage an empty folder: the build works and
 *                                            the app offers "connect to a server" only
 *
 * The bundle must hold its Node (node/bin/node) and the launcher (launcher/src/main.ts); there is no Windows bundle.
 */
import { cpSync, existsSync, mkdirSync, rmSync, writeFileSync, mkdtempSync } from 'fs';
import { spawnSync } from 'child_process';
import { tmpdir } from 'os';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const DEST = join(ROOT, 'resources', 'pipeshub-native');

rmSync(DEST, { recursive: true, force: true });
mkdirSync(DEST, { recursive: true });

let source = process.env.PIPESHUB_NATIVE_BUNDLE;
const url = process.env.PIPESHUB_NATIVE_BUNDLE_URL;
if (!source && url) {
  const work = mkdtempSync(join(tmpdir(), 'pipeshub-native-'));
  const archive = join(work, 'bundle.tar.gz');
  const dl = await fetch(url);
  if (!dl.ok) throw new Error(`download failed: ${dl.status}`);
  writeFileSync(archive, Buffer.from(await dl.arrayBuffer()));
  const x = spawnSync('tar', ['-xzf', archive, '-C', work], { stdio: 'inherit' });
  if (x.status !== 0) throw new Error('tar failed');
  rmSync(archive);
  source = work;
}

if (source) {
  cpSync(source, DEST, { recursive: true, dereference: false, verbatimSymlinks: true });
  for (const need of ['node/bin/node', 'launcher/src/main.ts']) {
    if (!existsSync(join(DEST, need))) throw new Error(`${need} not found in the bundle at ${source}`);
  }
  console.log(`Staged launcher bundle from ${source}`);
} else {
  console.log('No launcher bundle given: building without local mode (server mode only).');
}
