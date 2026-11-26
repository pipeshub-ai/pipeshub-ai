/**
 * Every MongoDB collection the application defines through Mongoose, by name. A test compares this list with the mappings so
 * that a collection added to the application cannot be left out of the migration. The model files are found by looking for them
 * (every `*.schema.ts` and every file in a `schema/` directory under the source tree, plus the few models that live elsewhere), not
 * from a hand-kept list: loading a model file registers its collection name, so the list cannot drift from the code.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import mongoose from 'mongoose';

/** Models that are declared outside a schema file (path relative to the source root, without extension). */
export const EXTRA_MODEL_FILES = [
  'libs/repositories/slug/mongo',
  'libs/repositories/agent-harness/control-plane-liveness/mongo',
];

/** Collections the migrator itself writes (the fence and the follow marker): never application data. */
export const MIGRATOR_COLLECTIONS = [
  'docstore_fence',
  'docstore_migrate_marker',
];

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

const COLLECTION_CALL =
  /\.collection(?:<[^>()]*>)?\(\s*(?:'([\w-]+)'|"([\w-]+)"|([A-Z][A-Z0-9_]*))\s*[,)]/g;

/**
 * Collections the source reaches through the driver by name (`db.collection('x')`, or a constant of the same file such as
 * `db.collection(AUDIT_RECORDS)`): a collection no Mongoose model declares still holds data a move must account for. A call that
 * names its collection through a variable is not seen; the erasure inventory (ORG_STORE_COVERAGE) covers those. The migrator's
 * own files are not read: they name the collections they copy and their own markers.
 */
export function driverCollections(root: string = SOURCE_ROOT): string[] {
  const out = new Set<string>();
  const walk = (dir: string): void => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      if (e.name === 'node_modules' || e.name === 'tests') continue;
      const full = join(dir, e.name);
      if (e.isDirectory()) {
        if (full !== __dirname) walk(full);
      } else if (CODE.test(e.name) && !e.name.endsWith('.d.ts')) {
        const text = readFileSync(full, 'utf8');
        for (const m of text.matchAll(COLLECTION_CALL)) {
          const literal = m[1] ?? m[2];
          if (literal) out.add(literal);
          else if (m[3]) {
            const def = new RegExp(
              `(?:const|exports\\.)\\s*${m[3]}\\s*=\\s*['"]([\\w-]+)['"]`,
            ).exec(text);
            if (def?.[1]) out.add(def[1]);
          }
        }
      }
    }
  };
  walk(root);
  return [...out].sort();
}
