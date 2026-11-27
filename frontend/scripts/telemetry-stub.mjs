// Prepares the frontend for a build without telemetry: puts the Data Collection
// stub in place of the real file and drops the text only the real file shows.
// It edits the tree it runs in, so run it only in a throwaway copy (an image build, CI).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const dir = path.join(root, 'app/(main)/workspace/general');
fs.renameSync(path.join(dir, 'data-collection.stub.tsx'), path.join(dir, 'data-collection.tsx'));

const isRemoved = (key) => /^(dataCollection|enableDataCollection)/.test(key);
let removed = 0;
const walk = (folder) => {
  for (const entry of fs.readdirSync(folder, { withFileTypes: true })) {
    const file = path.join(folder, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== 'node_modules') walk(file);
    } else if (entry.name.endsWith('.json')) {
      const json = JSON.parse(fs.readFileSync(file, 'utf8').replace(/^﻿/, ''));
      const general = json?.workspace?.general;
      const keys = Object.keys(general ?? {}).filter(isRemoved);
      if (keys.length === 0) continue;
      for (const key of keys) delete general[key];
      removed += keys.length;
      fs.writeFileSync(file, `${JSON.stringify(json, null, 2)}\n`);
    }
  }
};
const found = fs
  .readdirSync(root, { withFileTypes: true })
  .filter((e) => e.isDirectory() && e.name !== 'node_modules')
  .flatMap((e) => [path.join(root, e.name, 'i18n'), path.join(root, e.name, 'lib/i18n')])
  .concat(path.join(root, 'lib/i18n'))
  .filter((p) => fs.existsSync(p));
[...new Set(found)].forEach(walk);

if (removed === 0) {
  console.error('telemetry-stub: no translation keys found to remove; check the paths');
  process.exit(1);
}
console.log(`telemetry-stub: stub in place, ${removed} translation entries removed`);
