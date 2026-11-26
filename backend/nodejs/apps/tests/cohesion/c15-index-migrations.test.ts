import 'reflect-metadata';
import { expect } from 'chai';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import type { Model } from 'mongoose';
import { HARNESS_MIGRATIONS } from '../../src/libs/repositories/agent-harness/migrations/mongo';
import { CONTROL_PLANE_MIGRATIONS } from '../../src/libs/repositories/control-plane/migrations/mongo';
import {
  IndexDefinition,
  indexLabel,
} from '../../src/libs/utils/mongo-index-migration';

/**
 * C-15 (D2): every index an agent-harness model declares is built by a
 * registered migration's `up` (and so dropped by its `down`), and nothing
 * else builds them: the schemas opt out of `autoIndex`. The replica-set half
 * (the migrations' lists are what `up` really builds and `down` drops) is
 * tests/modules/agent-harness/harness-migrations.mongo.itest.ts.
 */

const HARNESS_SRC = join(__dirname, '../../src/modules/agent-harness');
// The control plane's models follow the same rule against its own migration list (CPD-2).
const MODEL_ROOTS = [
  HARNESS_SRC,
  join(__dirname, '../../src/modules/control-plane'),
  join(__dirname, '../../src/libs/repositories/agent-harness'),
  join(__dirname, '../../src/libs/repositories/control-plane'),
];
const MIGRATIONS = [...HARNESS_MIGRATIONS, ...CONTROL_PLANE_MIGRATIONS];
const MODEL_CALL =
  /mongoose\.model\s*(?:<[^>]*>)?\(\s*['"]([A-Za-z0-9_]+)['"]|\bmodel\(\s*['"]([A-Za-z0-9_]+)['"]/g;
/** Index options IndexDefinition carries; anything else must be added there first. */
const KNOWN_OPTIONS = new Set([
  'name',
  'unique',
  'partialFilterExpression',
  'expireAfterSeconds',
  'expires',
  'background',
]);

const sourceFiles = (directory: string): string[] =>
  readdirSync(directory).flatMap((name) => {
    const path = join(directory, name);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return name.endsWith('.ts') ? [path] : [];
  });

const isModel = (value: unknown): value is Model<unknown> =>
  typeof value === 'function' &&
  typeof (value as Partial<Model<unknown>>).modelName === 'string' &&
  typeof (value as Partial<Model<unknown>>).schema?.indexes === 'function';

interface DiscoveredModels {
  models: Map<string, { model: Model<unknown>; file: string }>;
  /** Model names a file defines that no file exports. */
  hidden: Array<{ name: string; file: string }>;
}

function discoverModels(): DiscoveredModels {
  const defining = MODEL_ROOTS.flatMap(sourceFiles).filter((file) =>
    /new (?:mongoose\.)?Schema\b/.test(readFileSync(file, 'utf8')),
  );
  const models = new Map<string, { model: Model<unknown>; file: string }>();
  const names: Array<{ name: string; file: string }> = [];
  for (const file of defining) {
    for (const match of readFileSync(file, 'utf8').matchAll(MODEL_CALL)) {
      names.push({ name: match[1] ?? match[2]!, file });
    }
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- the files are discovered at run time
    const exported = require(file) as Record<string, unknown>;
    for (const value of Object.values(exported)) {
      if (isModel(value)) models.set(value.modelName, { model: value, file });
    }
  }
  return {
    models,
    hidden: names.filter(({ name }) => !models.has(name)),
  };
}

const defaultIndexName = (key: Record<string, unknown>): string =>
  Object.entries(key)
    .map(([field, direction]) => `${field}_${String(direction)}`)
    .join('_');

/** A comparable form: collection.name → the options that change what the index enforces. */
const fingerprint = (index: IndexDefinition): string =>
  JSON.stringify({
    key: Object.entries(index.key),
    unique: index.unique === true,
    partialFilterExpression: index.partialFilterExpression ?? null,
    expireAfterSeconds: index.expireAfterSeconds ?? null,
  });

function declaredIndexes(models: DiscoveredModels['models']): {
  indexes: Map<string, IndexDefinition>;
  unsupported: string[];
} {
  const indexes = new Map<string, IndexDefinition>();
  const unsupported: string[] = [];
  for (const { model } of models.values()) {
    const collection = model.collection.collectionName;
    for (const [key, options] of model.schema.indexes() as Array<
      [Record<string, 1 | -1>, Record<string, unknown>]
    >) {
      const name =
        typeof options['name'] === 'string'
          ? options['name']
          : defaultIndexName(key);
      const index: IndexDefinition = {
        collection,
        name,
        key,
        ...(options['unique'] === true ? { unique: true } : {}),
        ...(options['partialFilterExpression'] !== undefined
          ? {
              partialFilterExpression: options[
                'partialFilterExpression'
              ] as Record<string, unknown>,
            }
          : {}),
        ...(typeof options['expireAfterSeconds'] === 'number'
          ? { expireAfterSeconds: options['expireAfterSeconds'] }
          : {}),
      };
      for (const option of Object.keys(options)) {
        if (!KNOWN_OPTIONS.has(option)) {
          unsupported.push(`${indexLabel(index)} (${option})`);
        }
      }
      indexes.set(indexLabel(index), index);
    }
  }
  return { indexes, unsupported };
}

describe('C-15 every agent-harness index is owned by a registered migration', () => {
  const discovered = discoverModels();
  const declared = declaredIndexes(discovered.models);

  it('finds the harness models', () => {
    expect(discovered.models.size).to.be.greaterThan(10);
    expect(declared.indexes.size).to.be.greaterThan(20);
  });

  it('C-15b: sees every model a harness file defines', () => {
    expect(
      discovered.hidden.map(
        ({ name, file }) =>
          `${name} (${relative(HARNESS_SRC, file)}) is not exported, so its indexes are not checked`,
      ),
    ).to.deep.equal([]);
  });

  it('C-15c: no harness schema builds its own indexes (autoIndex: false)', () => {
    const building = [...discovered.models.values()]
      .filter(({ model }) => model.schema.get('autoIndex') !== false)
      .map(
        ({ model, file }) =>
          `${model.modelName} (${relative(HARNESS_SRC, file)}) needs autoIndex: false; its indexes belong in a migration`,
      );
    expect(building).to.deep.equal([]);
  });

  it('every declared index option is one a migration can express', () => {
    expect(declared.unsupported).to.deep.equal([]);
  });

  it('C-15a: each declared index is in exactly one migration, with the same key and options', () => {
    const owned = new Map<string, { index: IndexDefinition; by: string }>();
    const problems: string[] = [];
    const ids = new Set<string>();
    for (const migration of MIGRATIONS) {
      if (ids.has(migration.id))
        problems.push(`migration id ${migration.id} is registered twice`);
      ids.add(migration.id);
      for (const index of migration.indexes) {
        const label = indexLabel(index);
        const earlier = owned.get(label);
        if (earlier !== undefined) {
          problems.push(
            `${label} is built by both ${earlier.by} and ${migration.id}`,
          );
          continue;
        }
        owned.set(label, { index, by: migration.id });
      }
    }
    for (const [label, index] of declared.indexes) {
      const found = owned.get(label);
      if (found === undefined) {
        problems.push(
          `${label} is declared on a model but no registered migration creates it`,
        );
      } else if (fingerprint(found.index) !== fingerprint(index)) {
        problems.push(
          `${label} differs between the model (${fingerprint(index)}) and migration ${found.by} (${fingerprint(found.index)})`,
        );
      }
    }
    for (const [label, { by }] of owned) {
      if (!declared.indexes.has(label)) {
        problems.push(
          `${label} is created by migration ${by} but no model declares it`,
        );
      }
    }
    expect(problems).to.deep.equal([]);
  });

  it('C-15d: a retired index is one no migration builds and no model declares', () => {
    const built = new Set(MIGRATIONS.flatMap((migration) => migration.indexes.map(indexLabel)));
    const problems: string[] = [];
    for (const migration of MIGRATIONS) {
      for (const index of migration.retired) {
        const label = `${index.collection}.${index.name}`;
        if (built.has(label)) problems.push(`${label} is retired by ${migration.id} and built by a migration: every boot would drop and rebuild it`);
        if (declared.indexes.has(label)) problems.push(`${label} is retired by ${migration.id} but a model still declares it`);
      }
    }
    expect(problems).to.deep.equal([]);
    expect(MIGRATIONS.flatMap((migration) => migration.retired.map((index) => `${index.collection}.${index.name}`))).to.include(
      'cpBindings.cp_bindings_owner',
    );
  });
});
