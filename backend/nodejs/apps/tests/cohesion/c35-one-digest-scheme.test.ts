/**
 * C-35 (05-migration-plan §6.3, CPD-3), Node half: one digest scheme. A digest
 * over JSON is the canonical one (RFC 8785 + SHA-256, `libs/utils/canonical-json`);
 * nothing in the agent harness or the control plane hashes `JSON.stringify`
 * text of its own. `ALLOWED` lists today's hand-rolled digests with why each is
 * not canonical yet; it only shrinks, and a stale entry fails. The Python half is
 * `backend/python/tests/cohesion/test_c35_one_digest_scheme.py`.
 */
import { expect } from 'chai';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import * as ts from 'typescript';

const APP = join(__dirname, '..', '..');
const ROOTS = [
  'src/modules/agent-harness',
  'src/modules/control-plane',
  // their Mongo repositories
  'src/libs/repositories/agent-harness',
  'src/libs/repositories/control-plane',
];

/** file:function -> why it is not the canonical digest yet. */
const ALLOWED: Readonly<Record<string, string>> = {
  'src/modules/agent-harness/domain/workspace.ts:manifestContentHash':
    "workspace manifest hashes stored with each revision and computed the same way by Python's " +
    '`workspace_manifest_content_hash`; a new scheme needs a manifest migration',
};

const isStringify = (node: ts.Node): boolean =>
  ts.isCallExpression(node) &&
  ts.isPropertyAccessExpression(node.expression) &&
  node.expression.name.text === 'stringify' &&
  ts.isIdentifier(node.expression.expression) &&
  node.expression.expression.text === 'JSON';

const isSha256Hasher = (node: ts.Node): boolean => {
  let current: ts.Node = node;
  while (ts.isCallExpression(current) || ts.isPropertyAccessExpression(current)) {
    if (
      ts.isCallExpression(current) &&
      (ts.isIdentifier(current.expression)
        ? current.expression.text === 'createHash'
        : ts.isPropertyAccessExpression(current.expression) && current.expression.name.text === 'createHash') &&
      current.arguments[0] !== undefined &&
      ts.isStringLiteral(current.arguments[0]) &&
      current.arguments[0].text === 'sha256'
    ) {
      return true;
    }
    current = current.expression;
  }
  return false;
};

const some = (node: ts.Node, test: (n: ts.Node) => boolean): boolean =>
  test(node) || (ts.forEachChild(node, (child) => (some(child, test) ? true : undefined)) ?? false);

const scopeName = (node: ts.Node): string => {
  for (let current: ts.Node | undefined = node; current !== undefined; current = current.parent) {
    if ((ts.isFunctionDeclaration(current) || ts.isMethodDeclaration(current)) && current.name !== undefined) {
      return current.name.getText();
    }
    if ((ts.isArrowFunction(current) || ts.isFunctionExpression(current)) && ts.isVariableDeclaration(current.parent)) {
      return current.parent.name.getText();
    }
  }
  return '<module>';
};

/** Every `createHash('sha256')….update(x)` whose `x` is JSON text, by enclosing function. */
function handRolledDigests(fileName: string, text: string): string[] {
  const source = ts.createSourceFile(fileName, text, ts.ScriptTarget.Latest, true);
  const tainted = new Set<string>();
  const collect = (node: ts.Node): void => {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer && some(node.initializer, isStringify)) {
      tainted.add(node.name.text);
    }
    ts.forEachChild(node, collect);
  };
  collect(source);
  const json = (node: ts.Node): boolean =>
    some(node, (n) => isStringify(n) || (ts.isIdentifier(n) && tainted.has(n.text)));
  const found = new Set<string>();
  const visit = (node: ts.Node): void => {
    if (
      ts.isCallExpression(node) &&
      ts.isPropertyAccessExpression(node.expression) &&
      node.expression.name.text === 'update' &&
      isSha256Hasher(node.expression.expression) &&
      node.arguments.some(json)
    ) {
      found.add(`${fileName}:${scopeName(node)}`);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return [...found].sort();
}

const files = (dir: string): string[] =>
  readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) return name === 'generated' ? [] : files(full);
    return name.endsWith('.ts') ? [full] : [];
  });

const scan = (): string[] =>
  ROOTS.flatMap((root) => files(join(APP, root)))
    .flatMap((file) => handRolledDigests(relative(APP, file), readFileSync(file, 'utf8')))
    .sort();

describe('C-35 one digest scheme (Node)', () => {
  it('nothing hashes JSON text outside the canonical helper', () => {
    expect(scan().filter((hit) => !(hit in ALLOWED))).to.deep.equal([]);
  });

  it('every allowed entry still hashes JSON text', () => {
    const hits = new Set(scan());
    expect(Object.keys(ALLOWED).filter((entry) => !hits.has(entry))).to.deep.equal([]);
  });

  it('the scan finds each form and nothing else', () => {
    const planted = [
      "import { createHash } from 'node:crypto';",
      "function direct(x: unknown) { return createHash('sha256').update(JSON.stringify(x)).digest('hex'); }",
      "const named = (x: unknown) => { const text = JSON.stringify(x); return createHash('sha256').update(text, 'utf8').digest('hex'); };",
      "function namespaced(x: unknown) { return crypto.createHash('sha256').update(JSON.stringify(x)).digest('hex'); }",
      "function fine(raw: Buffer) { return createHash('sha256').update(raw).digest('hex'); }",
      "function md5(x: unknown) { return createHash('md5').update(JSON.stringify(x)).digest('hex'); }",
    ].join('\n');
    expect(handRolledDigests('planted.ts', planted)).to.deep.equal([
      'planted.ts:direct',
      'planted.ts:named',
      'planted.ts:namespaced',
    ]);
  });
});
