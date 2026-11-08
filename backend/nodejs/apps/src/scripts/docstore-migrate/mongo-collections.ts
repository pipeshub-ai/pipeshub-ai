/**
 * Every MongoDB collection the application defines through Mongoose, by name. A test compares this list with the mappings so
 * that a collection added to the application cannot be left out of the migration. The model files are found by looking for them
 * (every `*.schema.ts` and every file in a `schema/` directory under the source tree, plus the few models that live elsewhere), not
 * from a hand-kept list: loading a model file registers its collection name, so the list cannot drift from the code.
 */
import { readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import mongoose from 'mongoose';

/** Models that are declared outside a schema file (path relative to the source root, without extension). */
export const EXTRA_MODEL_FILES = ['libs/repositories/slug/mongo'];

const SOURCE_ROOT = resolve(__dirname, '../..');
const CODE = /\.(ts|js)$/;

/** Model files under `root`: `*.schema.*` anywhere, and any code file inside a directory named `schema`. */
export function findModelFiles(root: string = SOURCE_ROOT): string[] {
  const out: string[] = [];
  const walk = (dir: string, inSchemaDir: boolean): void => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      if (e.name === 'node_modules' || e.name === 'tests') continue;
      const full = join(dir, e.name);
      if (e.isDirectory()) walk(full, e.name === 'schema');
      else if (
        CODE.test(e.name) &&
        !e.name.endsWith('.d.ts') &&
        !e.name.endsWith('.map') &&
        (inSchemaDir || /\.schema\.(ts|js)$/.test(e.name))
      )
        out.push(full.slice(root.length + 1).replace(CODE, ''));
    }
  };
  walk(root, false);
  return [...new Set([...out, ...EXTRA_MODEL_FILES])].sort();
}

export function modelCollections(files: string[] = findModelFiles()): string[] {
  for (const f of files) {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- loading a model file registers its collection name
    require(join(SOURCE_ROOT, f));
  }
  return [
    ...new Set(Object.values(mongoose.models).map((m) => m.collection.name)),
  ].sort();
}

export const MONGO_COLLECTIONS: string[] = modelCollections();
