import { readFile, readdir, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';

const packageRoot = path.resolve('node_modules/pptxgenjs');
const packagePath = path.join(packageRoot, 'package.json');
const metadata = JSON.parse(await readFile(packagePath, 'utf8'));

// image-size has no patched npm release for GHSA-w3rx-r6r6-pgpr and
// GHSA-5p2g-fcmc-qvqq. PptxGenJS 4.0.1 declares it but does not import it.
// Fail on any upstream graph or bundle change instead of silently applying an
// unsafe patch to a new release.
if (
  metadata.version !== '4.0.1' ||
  metadata.dependencies?.['image-size'] !== '^1.2.1'
) {
  throw new Error('Refusing to patch an unreviewed PptxGenJS dependency graph');
}

const distRoot = path.join(packageRoot, 'dist');
for (const name of await readdir(distRoot)) {
  const contents = await readFile(path.join(distRoot, name), 'utf8');
  if (contents.includes('image-size')) {
    throw new Error(`PptxGenJS runtime unexpectedly imports image-size in ${name}`);
  }
}

delete metadata.dependencies['image-size'];
await writeFile(packagePath, `${JSON.stringify(metadata, null, 2)}\n`);
await rm(path.resolve('node_modules/image-size'), { recursive: true, force: true });
