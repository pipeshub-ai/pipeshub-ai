import 'reflect-metadata';
import { expect } from 'chai';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import * as ts from 'typescript';
import { CONTROL_PLANE_DATA_MIGRATIONS } from '../../src/modules/control-plane';

/**
 * C-15, the Node data half (CPD-4; the Python half is
 * backend/python/tests/cohesion/test_c15_data_migrations.py): a class in the
 * control plane or the agent harness that rewrites stored documents (it has
 * both `apply` and `revert`) is in `CONTROL_PLANE_DATA_MIGRATIONS` under its
 * own id, reports before it writes (`dryRun`), runs by mode and keeps a ledger.
 */

const SRC = join(__dirname, '../../src');
const ROOTS = ['modules/control-plane', 'modules/agent-harness'].map((root) => join(SRC, root));

const sourceFiles = (directory: string): string[] =>
  readdirSync(directory).flatMap((name) => {
    const path = join(directory, name);
    if (statSync(path).isDirectory()) return name === 'generated' ? [] : sourceFiles(path);
    return name.endsWith('.ts') ? [path] : [];
  });

export function migrationClasses(files: ReadonlyArray<{ path: string; text: string }>): string[] {
  const found: string[] = [];
  for (const { path, text } of files) {
    const source = ts.createSourceFile(path, text, ts.ScriptTarget.Latest, true);
    source.forEachChild(function visit(node) {
      if (ts.isClassDeclaration(node) && node.name !== undefined) {
        const methods = new Set(
          node.members
            .filter(ts.isMethodDeclaration)
            .map((member) => member.name.getText(source)),
        );
        if (methods.has('apply') && methods.has('revert')) found.push(`${path}:${node.name.text}`);
      }
      node.forEachChild(visit);
    });
  }
  return found.sort();
}

describe('C-15 data migrations (Node)', () => {
  const files = ROOTS.flatMap(sourceFiles).map((path) => ({
    path: relative(SRC, path),
    text: readFileSync(path, 'utf8'),
  }));

  it('MG-7: every class with apply and revert is a registered data migration', () => {
    const registered = Object.values(CONTROL_PLANE_DATA_MIGRATIONS).map((migration) => migration.name).sort();
    expect(migrationClasses(files).map((entry) => entry.split(':')[1])).to.deep.equal(registered);
  });

  it('MG-7: each registered migration has its id, dryRun, apply, revert, run and a ledger key', () => {
    expect(Object.keys(CONTROL_PLANE_DATA_MIGRATIONS)).to.include('control-plane-capabilities-v1');
    for (const [id, migration] of Object.entries(CONTROL_PLANE_DATA_MIGRATIONS)) {
      const instance = Object.create(migration.prototype as object) as Record<string, unknown>;
      for (const method of ['dryRun', 'apply', 'revert', 'run']) {
        expect(instance[method], `${id}.${method}`).to.be.a('function');
      }
      const built = new (migration as unknown as new (options: unknown) => { id: string; ledgerKey: string })({});
      expect(built.id).to.equal(id);
      expect(built.ledgerKey).to.match(new RegExp(`^cp[A-Za-z]*Ledger/${id}$`));
    }
    const keys = Object.values(CONTROL_PLANE_DATA_MIGRATIONS).map(
      (migration) => (new (migration as unknown as new (options: unknown) => { ledgerKey: string })({})).ledgerKey,
    );
    expect(new Set(keys).size).to.equal(keys.length);
  });

  it('MG-7: the scan finds a planted migration class', () => {
    const planted = [{ path: 'modules/agent-harness/x.ts', text: 'export class Rewrite { async apply() {} async revert() {} }' }];
    expect(migrationClasses(planted)).to.deep.equal(['modules/agent-harness/x.ts:Rewrite']);
  });
});
