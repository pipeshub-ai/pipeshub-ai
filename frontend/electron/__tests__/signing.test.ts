import test from 'node:test';
import * as assert from 'node:assert/strict';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

// eslint-disable-next-line @typescript-eslint/no-require-imports
const afterPack = require(path.resolve(__dirname, '..', '..', '..', 'scripts', 'electron', 'after-pack.cjs'));

test('every Mach-O file in the launcher bundle is listed for signing, nothing else', async () => {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'ph-sign-'));
  const bundle = path.join(out, 'PipesHub.app', 'Contents', 'Resources', 'pipeshub-native');
  fs.mkdirSync(path.join(bundle, 'py', 'lib'), { recursive: true });
  fs.mkdirSync(path.join(bundle, 'pg', 'bin'), { recursive: true });
  const thin = Buffer.from('cffaedfe0c000001', 'hex'); // arm64 Mach-O header start
  fs.writeFileSync(path.join(bundle, 'pipeshub-native'), thin);
  fs.writeFileSync(path.join(bundle, 'py', 'lib', 'x.cpython-312-darwin.so'), thin);
  fs.writeFileSync(path.join(bundle, 'pg', 'bin', 'postgres'), Buffer.from('cafebabe00000002', 'hex')); // fat
  fs.writeFileSync(path.join(bundle, 'py', 'lib', 'module.py'), 'print(1)\n');
  fs.writeFileSync(path.join(bundle, 'pg', 'bin', 'tiny'), 'ab');
  fs.symlinkSync(path.join(bundle, 'pipeshub-native'), path.join(bundle, 'link'));

  const options: { binaries?: string[] } = { binaries: ['already/there'] };
  const count = await afterPack.addBundleBinariesToSigning({
    appOutDir: out,
    packager: { appInfo: { productFilename: 'PipesHub' }, platformSpecificBuildOptions: options },
  });
  assert.equal(count, 3);
  assert.deepEqual(
    (options.binaries as string[]).slice(1).map((p) => path.relative(bundle, p)).sort(),
    ['pg/bin/postgres', 'pipeshub-native', 'py/lib/x.cpython-312-darwin.so'],
  );
  assert.equal(options.binaries?.[0], 'already/there');
  // No bundle (a build without local mode): nothing to add.
  assert.equal(await afterPack.addBundleBinariesToSigning({
    appOutDir: path.join(out, 'none'),
    packager: { appInfo: { productFilename: 'PipesHub' }, platformSpecificBuildOptions: {} },
  }), 0);
  fs.rmSync(out, { recursive: true, force: true });
});
